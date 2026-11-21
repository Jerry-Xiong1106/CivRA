import json
from pathlib import Path

from backend.agent.model.llm import ScriptedLLM, tool_call
from backend.common.schemas import LLMResponse
from evaluation.adapters.team import ModelAdapter, RetrievalAdapter
from evaluation.runners.consultation import ConsultationPilot, PilotDisclosure, citation_bindings, delivery_status
from evaluation.schemas.tasks import HiddenFact, HiddenTask, load_public_tasks


ROOT = Path(__file__).resolve().parents[2]


class EmptyRetrieval:
    def search(self, query, top_k):
        return {"status": "no_results", "results": [], "reason": "no_candidates"}


def test_citation_checks_never_infer_a_binding_from_an_article_number():
    matches = [{"source_ref": {"source_id": "law::705", "source_version": "2020-05-28"}}]
    text = "第七百零五条\n依据 [law::705] [law::705]\n[law::invented]"
    sources, bindings, unknown = citation_bindings(text, matches)
    assert bindings == [{"start": text.index("依据"), "end": text.index("\n[law::invented]"), "source_ref": sources[0]}]
    assert unknown == ["law::invented"]
    assert citation_bindings("参见第七百零五条", matches)[1] == []


def test_fixed_and_agent_share_disclosure_rule_without_revealing_unrelated_facts(tmp_path):
    task = load_public_tasks(ROOT / "evaluation/datasets/pilot/tasks.jsonl")[1]
    hidden = HiddenTask(task_id=task.task_id, dataset_version=task.dataset_version, facts=[HiddenFact(
        fact_id="partial-repayment", disclosure_condition="Only on a repayment request",
        accepted_question_examples=["是否已还款？"], response="PRIVATE_TEST_REPLY",
    )])
    controller = PilotDisclosure(hidden)
    assert controller.ask("借款何时到期？") != "PRIVATE_TEST_REPLY"
    assert controller.ask("请提供还款日期。") != "PRIVATE_TEST_REPLY"
    assert controller.decisions[-1]["fact_id"] is None

    config = json.loads((ROOT / "evaluation/configs/consultation_pilot_v2.json").read_text())
    model = ModelAdapter(ScriptedLLM([
        LLMResponse(content="固定回答", finish_reason="stop"),
        tool_call("ask_user", questions=[{"question_id": "q1", "text": "是否已还款？"}]),
        tool_call("finish", answer="继续回答"),
    ]))
    pilot = ConsultationPilot(model, RetrievalAdapter(EmptyRetrieval()), {task.task_id: hidden}, tmp_path)
    fixed = pilot.run(task, "fixed_workflow", config, "test-fixed")
    agent = pilot.run(task, "agent", config, "test-agent")
    assert fixed["status"] == agent["status"] == "completed"
    assert fixed["verification"]["citation_audit"]["semantic_support_checked"] is False
    assert [d["fact_id"] for d in fixed["disclosure_decisions"]] == ["partial-repayment"]
    assert [d["fact_id"] for d in agent["disclosure_decisions"]] == ["partial-repayment"]
    assert "PRIVATE_TEST_REPLY" not in json.dumps(model.client.calls[1], ensure_ascii=False)
    assert "PRIVATE_TEST_REPLY" in json.dumps(model.client.calls[2], ensure_ascii=False)


def test_technical_completion_does_not_count_as_a_delivered_answer():
    assert delivery_status("completed", " \n") == "empty_output"
    assert delivery_status("completed", "有正文") == "completed"
    assert delivery_status("failed", "保留的部分输出") == "failed"
