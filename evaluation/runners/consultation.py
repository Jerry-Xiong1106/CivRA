"""Four-setting consultation pilot over the integrated CivRA interfaces."""

import json
import re
import time
from datetime import datetime, timedelta, timezone
from uuid import uuid4

from backend.agent.factory import build_runner
from backend.case import CaseService, SQLiteCaseRepository
from backend.case.models import CaseSnapshot
from backend.case.research.knowledge_bridge import CorpusSourceResolver
from backend.common.schemas import UserReply
from evaluation.adapters.team import AgentAdapter
from evaluation.baselines.consultation import run_baseline
from evaluation.config import run_config_from_pilot


MODES = ["direct_llm", "rag", "fixed_workflow", "agent"]
REPAYMENT_QUESTION = "是否已经还过款？如果有，已还多少本金？"
REPAYMENT = re.compile(r"还款|归还|还过|还了|还钱|偿还|偿付|repa(id|y|yment)", re.IGNORECASE)
REQUEST = re.compile(r"是否|有无|多少|有没有|还过|(?:已|曾).{0,5}(?:了吗|么|[?？])|\b(any|whether|how much)\b", re.IGNORECASE)
SOURCE_MARKER = re.compile(r"\[([^\[\]\r\n]{1,300})\]")


class PilotDisclosure:
    """Explicit policy for the existing pilot's single repayment fact."""

    def __init__(self, hidden_task):
        self.facts = {fact.fact_id: fact.response for fact in hidden_task.facts}
        if set(self.facts) - {"partial-repayment"} or hidden_task.route_choice:
            raise ValueError("This consultation pilot only supports the repayment disclosure rule")
        self.decisions = []

    def ask(self, text, *, question_id=None, wait_id=None):
        fact_id = ("partial-repayment" if "partial-repayment" in self.facts
                   and REPAYMENT.search(text) and REQUEST.search(text) else None)
        reply = self.facts[fact_id] if fact_id else "暂时没有可补充的信息。"
        self.decisions.append(dict(question=text, question_id=question_id, wait_id=wait_id,
                                   fact_id=fact_id, reply=reply))
        return reply

    def reply(self, wait):
        if wait.kind not in {"clarification", "material_request"}:
            raise ValueError("Route steps are outside the consultation pilot")
        answers = {q.question_id: self.ask(q.text, question_id=q.question_id, wait_id=wait.wait_id)
                   for q in wait.questions}
        return UserReply(answers=answers) if answers else UserReply(text="暂时没有可补充的信息。")


def citation_bindings(answer, matches):
    """Bind explicit IDs to their line spans without inventing missing citations."""
    sources = {}
    for hit in matches:
        ref = hit["source_ref"]
        sources[ref["source_id"]] = {key: ref[key] for key in ("source_id", "source_version")}
    bindings, unknown, seen = [], [], set()
    for marker in SOURCE_MARKER.finditer(answer):
        source_id = marker.group(1)
        if source_id not in sources:
            if "::" in source_id or source_id.startswith(("source_", "source:", "src_")):
                unknown.append(source_id)
            continue
        start = answer.rfind("\n", 0, marker.start()) + 1
        end = answer.find("\n", marker.end())
        end = len(answer) if end == -1 else end
        key = (source_id, start, end)
        if key not in seen:
            bindings.append(dict(start=start, end=end, source_ref=sources[source_id]))
            seen.add(key)
    return list(sources.values()), bindings, sorted(set(unknown))


def delivery_status(status, answer):
    if status == "completed" and (not isinstance(answer, str) or not answer.strip()):
        return "empty_output"
    return status


class ConsultationPilot:
    def __init__(self, model, retrieval, hidden_tasks, state_dir):
        self.model = model
        self.retrieval = retrieval
        self.hidden_tasks = hidden_tasks
        self.repository = SQLiteCaseRepository(state_dir / "cases.sqlite3")
        self.service = CaseService(self.repository, source_resolver=CorpusSourceResolver.from_directory())
        self.setup = build_runner(llm=model.client, case_service=self.service,
                                  retrieval_service=retrieval.service, dotenv=None)

    def run(self, task, mode, config, experiment_run_id):
        if mode in {"direct_llm", "rag"}:
            result = run_baseline(task, mode, self.model, config, self.retrieval)
            if mode == "rag" and result["output"] and result["retrieval"]:
                _, bindings, unknown = citation_bindings(result["output"], result["retrieval"]["matches"])
                result["citation_check"] = {"bindings": bindings, "unknown_ids": unknown}
            return result

        start = time.monotonic()
        now = datetime.now(timezone.utc)
        case = self.repository.create_case(CaseSnapshot(
            case_id="eval_" + uuid4().hex, case_version=1, synthetic=True, dispute_type=None,
            jurisdiction="CN-Mainland", stage="consultation", selected_route=None,
            created_at=now, updated_at=now,
        ))
        disclosure = PilotDisclosure(self.hidden_tasks[task.task_id])
        if mode == "agent":
            turns = config["budgets"]["max_interaction_turns"] if task.task_type == "interactive" else 0
            runtime = run_config_from_pilot(config).model_copy(update={"max_clarification_rounds": turns})
            packet = json.dumps(task.to_model_input(), ensure_ascii=False)
            envelope = AgentAdapter(self.setup.runner).run(case.case_id, packet, runtime,
                                                          experiment_run_id, disclosure.reply, max_turns=turns)
            info = envelope["run"]
            usage = info["usage"]
            answer = (info["final"] or {}).get("answer")
            model_stops = []
            for step in envelope["trace"]:
                if step["step_type"] == "model_call" and step["result_ref"]:
                    detail = self.setup.runner.get_step_result(info["run_id"], step["result_ref"])
                    model_stops.append({"step_id": step["step_id"],
                                        "finish_reason": (detail.get("response") or {}).get("finish_reason")})
            return {**envelope, "status": info["status"], "output": answer,
                    "delivery_status": delivery_status(info["status"], answer), "model_stops": model_stops,
                    "usage": usage, "model_calls": usage["model_calls"], "duration_ms": usage["duration_ms"],
                    "disclosure_decisions": disclosure.decisions}

        if mode != "fixed_workflow":
            raise ValueError(f"Unsupported setting: {mode}")
        deadline = now + timedelta(seconds=config["budgets"]["max_active_seconds"])
        context = dict(case_id=case.case_id, experiment_run_id=experiment_run_id, deadline_at=deadline)
        evidence = self.service.check_evidence(dict(context={**context, "request_id": f"{case.case_id}:check"},
                                                   case_version=case.case_version, case_snapshot=case))
        if evidence.status == "error":
            return {"status": "failed", "output": None, "model_calls": 0,
                    "duration_ms": round((time.monotonic() - start) * 1000),
                    "error": {"type": "EvidenceCheckFailed", "code": evidence.error.code}}
        # The evidence tool sees the current structured snapshot. The generator still
        # receives the full public packet; empty structured fields are review hints.
        model_context = ["证据工具对当前结构化案件记录的检查（以公开案情和材料为准）：\n" + evidence.summary]
        if task.task_type == "interactive" and task.domain == "lending":
            reply = disclosure.ask(REPAYMENT_QUESTION, question_id="fixed-repayment")
            model_context.append(f"按固定流程核实的问题：{REPAYMENT_QUESTION}\n用户补充：{reply}")
        result = run_baseline(task, "rag", self.model, config, self.retrieval, context=model_context)
        result.update(evidence_check=evidence.model_dump(mode="json"), disclosure_decisions=disclosure.decisions)
        if result["output"] and result["retrieval"]:
            sources, bindings, unknown = citation_bindings(result["output"], result["retrieval"]["matches"])
            verified = self.service.review_answer(dict(
                context={**context, "request_id": f"{case.case_id}:review"}, case_version=case.case_version,
                answer=result["output"], retrieved_sources=sources, citations=bindings,
                requires_citations=False,
            ))
            result["citation_check"] = {"bindings": bindings, "unknown_ids": unknown}
            result["verification"] = {"status": verified.status, "summary": verified.summary,
                                      "citation_audit": verified.data.citation_audit if verified.data else None}
        result["duration_ms"] = round((time.monotonic() - start) * 1000)
        return result
