"""Evaluation names mapped to existing team interfaces; no duplicate algorithms."""

from backend.common.schemas import UserReply


class ModelAdapter:
    def __init__(self, client):
        self.client = client

    def generate(self, messages, config):
        response = self.client.chat(
            messages, tools=None, model=config["model"],
            temperature=config["temperature"], seed=config.get("seed"),
            max_tokens=config["max_output_tokens"],
        )
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
        matches = []
        for hit in response["results"]:
            matches.append({
                "source_id": hit["provision_id"], "title": hit["law_title"],
                "locator": hit["article_no"], "text": hit["text"],
                "source_version": hit["version"]["effective_date"],
                "page_start": hit["source"]["page_start"], "page_end": hit["source"]["page_end"],
                "source_file": hit["source"]["file"], "url": hit["source"]["url"],
                "rank": hit["retrieval"]["rank"], "score": hit["retrieval"]["score"],
            })
        return {"status": "ok", "matches": matches, "empty_reason": response.get("reason"),
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
        while info.status == "waiting_user":
            if len(disclosures) >= max_turns:
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
                "disclosures": disclosures,
                "manifest": self.runner.get_runtime_manifest().model_dump(mode="json")}
