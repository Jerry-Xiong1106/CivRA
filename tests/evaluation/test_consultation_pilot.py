import json
import os
from pathlib import Path

from backend.agent.model.llm import ScriptedLLM, tool_call
from backend.common.schemas import LLMResponse
from evaluation.adapters.team import ModelAdapter, RetrievalAdapter
from evaluation.baselines.consultation import run_baseline
from evaluation.runners.consultation import ConsultationPilot, PilotDisclosure, citation_bindings, delivery_status
from evaluation.runners.pilot import token_budget_audit
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
    assert delivery_status("completed", "未写完的正文", [{"finish_reason": "length"}]) == "truncated"
    assert delivery_status("completed", "正文", [{"finish_reason": "length"}, {"finish_reason": "stop"}]) == "completed"


def test_short_labels_expand_only_explicit_candidates_and_preserve_model_output():
    task = load_public_tasks(ROOT / "evaluation/datasets/pilot/tasks.jsonl")[0]
    config = json.loads((ROOT / "evaluation/configs/consultation_pilot_v3.json").read_text())
    ref = dict(source_id="law::705", title="法典", locator="第七百零五条", source_version="2020-05-28")

    class Retrieval:
        def search_statutes(self, query, top_k):
            return {"matches": [{"source_ref": ref, "text": "条文正文"}], "empty_reason": None}

    original = "结论 [S1]，未知候选 [S99]"
    model = ModelAdapter(ScriptedLLM([LLMResponse(content=original, finish_reason="stop")]))
    result = run_baseline(task, "rag", model, config, Retrieval())
    assert result["status"] == "completed"
    assert result["model_response"]["content"] == original
    assert result["output"] == "结论 [law::705]，未知候选 [S99]"
    assert result["citation_labels"]["unknown_labels"] == ["S99"]
    assert "law::705" not in result["messages"][-1]["content"]
    assert "条文正文" in result["messages"][-1]["content"]


def test_baseline_records_empty_length_stop_as_truncated():
    task = load_public_tasks(ROOT / "evaluation/datasets/pilot/tasks.jsonl")[0]
    config = json.loads((ROOT / "evaluation/configs/consultation_pilot_v3.json").read_text())
    model = ModelAdapter(ScriptedLLM([LLMResponse(content="", finish_reason="length")]))
    result = run_baseline(task, "direct_llm", model, config)
    assert result["status"] == "truncated"
    assert result["model_response"]["finish_reason"] == "length"
    assert result["error"] is None


def test_token_budget_checks_cumulative_usage_and_preserves_missing_usage():
    record = {"mode": "agent", "usage": {"prompt_tokens": 24000, "completion_tokens": 852, "tokens_complete": True}}
    assert token_budget_audit(record, 24000) == {"status": "exceeded", "total_tokens": 24852, "limit": 24000}
    assert token_budget_audit(record, None)["status"] == "not_limited"
    record["usage"]["tokens_complete"] = False
    assert token_budget_audit(record, 24000)["status"] == "unknown"
    baseline = {"mode": "rag", "model_response": {"usage": {"total_tokens": 24000}}}
    assert token_budget_audit(baseline, 24000)["status"] == "within_limit"


def test_deepseek_pilot_uses_its_own_key_and_leaves_embedding_key_untouched(monkeypatch):
    from scripts.evaluation.run_evaluation_pilot import pilot_client

    monkeypatch.setenv("DEEPSEEK_API_KEY", "deepseek-test-key")
    monkeypatch.setenv("SILICONFLOW_API_KEY", "embedding-test-key")
    monkeypatch.setenv("CIVRA_LLM_API_KEY", "app-test-key")
    monkeypatch.setenv("CIVRA_LLM_BASE_URL", "https://example.invalid")
    client = pilot_client({"provider": "deepseek", "model": "deepseek-flash"}, prompt_key=False)
    assert client.openai_api_base == "https://api.deepseek.com"
    assert client.openai_api_key.get_secret_value() == "deepseek-test-key"
    assert client.extra_body == {"thinking": {"type": "disabled"}}
    assert os.environ["SILICONFLOW_API_KEY"] == "embedding-test-key"
