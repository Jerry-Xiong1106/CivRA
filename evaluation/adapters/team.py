"""Evaluation names mapped to existing team interfaces; no duplicate algorithms."""

from time import perf_counter

from langchain_core.messages import convert_to_messages

from backend.agent.model.llm import response_from_message
from backend.knowledge.retrieval.results import to_search_match
from backend.common.schemas import SourceRef, UserReply


class ModelAdapter:
    def __init__(self, client):
        self.client = client

    def generate(self, messages, config):
        params = {"model": config["model"], "temperature": config["temperature"],
                  "seed": config.get("seed"), "max_tokens": config["max_output_tokens"]}
        tick = perf_counter()
        reply = self.client.bind(**{k: v for k, v in params.items() if v is not None}).invoke(
            convert_to_messages(messages))
        response = response_from_message(reply, self.client, round((perf_counter() - tick) * 1000))
        data = response.model_dump(mode="json")
        usage = data.pop("usage")
        data["usage"] = {
            "input_tokens": usage["prompt_tokens"], "output_tokens": usage["completion_tokens"],
            "total_tokens": usage["total_tokens"], "cached_tokens": usage["cached_tokens"],
            "attempt_count": usage["attempt_count"],
        }
        return data


class RetrievalAdapter:
    def __init__(self, service):
        self.service = service

    def search_statutes(self, query, top_k):
        response = self.service.search(query, top_k=top_k)
        if response["status"] not in ("ok", "no_results"):
            raise ValueError("Unrecognized retrieval status")
        if response.get("reason") in ("empty_query", "no_searchable_content", "unsupported_exact_query"):
            raise ValueError("Unsupported retrieval request")
        metadata = getattr(self.service, "source_metadata", {})
        matches = [to_search_match(hit, metadata) for hit in response["results"]]
        for match in matches:
            SourceRef.model_validate(match["source_ref"])
        return {"status": "ok", "schema_version": "0.1", "matches": matches,
                "knowledge_version": getattr(self.service, "knowledge_version", None),
                "empty_reason": response.get("reason"),
                "warnings": response.get("warnings", [])}


class AgentAdapter:
    """Run an Agent with an external disclosure callback; never accept gold records."""
    def __init__(self, runner):
        self.runner = runner

    def run(self, case_id, user_message, config, experiment_run_id, reply_to_wait, max_turns=3):
        info = self.runner.start_run(case_id, user_message, config=config,
                                     experiment_run_id=experiment_run_id,
                                     request_id=f"{experiment_run_id}:start")
        disclosures = []
        stop_reason = None
        while info.status == "waiting_user":
            if len(disclosures) >= max_turns:
                stop_reason = "interaction_limit"
                info = self.runner.cancel_run(info.run_id)
                break
            wait = info.wait
            reply = reply_to_wait(wait)
            if not isinstance(reply, UserReply):
                raise TypeError("Disclosure callback must return UserReply")
            disclosures.append({"wait": wait.model_dump(mode="json"), "reply": reply.model_dump(mode="json")})
            info = self.runner.resume_run(info.run_id, reply, wait_id=wait.wait_id,
                                         expected_case_version=info.case_version,
                                         request_id=f"{experiment_run_id}:{wait.wait_id}")
        steps, cursor = [], 0
        while True:
            page = self.runner.export_trace(info.run_id, cursor=cursor)
            steps.extend(step.model_dump(mode="json") for step in page.steps)
            if page.complete:
                break
            if page.next_cursor is None or page.next_cursor <= cursor:
                raise RuntimeError("Trace export did not advance")
            cursor = page.next_cursor
        return {"run": info.model_dump(mode="json"), "trace": steps,
                "disclosures": disclosures, "stop_reason": stop_reason,
                "manifest": self.runner.get_runtime_manifest().model_dump(mode="json")}
