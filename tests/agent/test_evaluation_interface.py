"""Behaviour Member 5's evaluation runner relies on (interface_requirements.md §5, §7)."""

from __future__ import annotations

import time

import pytest

from backend.agent.case_port import InMemoryCaseStore
from backend.agent.model.llm import ScriptedLLM, tool_call
from backend.agent.runner import AgentRunner, RunStateError, StaleWait, VersionConflict
from backend.agent.runtime.store import RunStore
from backend.common.schemas import CostInfo, LLMResponse, RunConfig, TokenUsage, UserReply
from tests.native_tools import mock_tool_fixture

CASE = "eval-case-001"
ASK = tool_call("ask_user", questions=[{"question_id": "q1", "text": "是否已经报保险？"}])


@pytest.fixture
def cases():
    store = InMemoryCaseStore()
    store.create(CASE, {"facts": {}})
    return store


def runner_for(cases, tmp_path, script, **kw):
    kw.setdefault("default_config", RunConfig(conversation_route="case_work"))
    return AgentRunner(ScriptedLLM(script), mock_tool_fixture(cases), cases, RunStore(tmp_path), **kw)


# ---------------------------------------------------------------- continue_run safety

def test_stale_wait_id_is_rejected_without_running(cases, tmp_path):
    runner = runner_for(cases, tmp_path, [ASK, tool_call("finish", answer="ok")])
    info = runner.start_run(CASE, "hi")
    calls_before = len(runner.llm.calls)
    with pytest.raises(StaleWait):
        runner.resume_run(info.run_id, UserReply(answers={"q1": "是"}), wait_id="wait-old")
    assert len(runner.llm.calls) == calls_before
    assert runner.get_run(info.run_id).status == "waiting_user"


def test_version_conflict_is_rejected_without_running(cases, tmp_path):
    runner = runner_for(cases, tmp_path, [ASK, tool_call("finish", answer="ok")])
    info = runner.start_run(CASE, "hi")
    cases.update_facts(CASE, {"x": 1})  # someone else changed the case meanwhile
    with pytest.raises(VersionConflict):
        runner.resume_run(info.run_id, UserReply(answers={"q1": "是"}), wait_id=info.wait.wait_id,
                          expected_case_version=info.case_version)
    assert runner.get_run(info.run_id).status == "waiting_user"


def test_repeated_resume_request_id_does_not_run_twice(cases, tmp_path):
    runner = runner_for(cases, tmp_path, [ASK, tool_call("finish", answer="ok")])
    info = runner.start_run(CASE, "hi")
    wait_id = info.wait.wait_id
    first = runner.resume_run(info.run_id, UserReply(answers={"q1": "是"}), wait_id=wait_id, request_id="req-7")
    calls = len(runner.llm.calls)
    again = runner.resume_run(info.run_id, UserReply(answers={"q1": "是"}), wait_id=wait_id, request_id="req-7")
    assert first.status == again.status == "completed"
    assert len(runner.llm.calls) == calls


def test_repeated_start_request_id_returns_same_run(cases, tmp_path):
    runner = runner_for(cases, tmp_path, [tool_call("finish", answer="ok")])
    a = runner.start_run(CASE, "hi", request_id="start-1", experiment_run_id="exp-1")
    b = runner.start_run(CASE, "hi", request_id="start-1", experiment_run_id="exp-1")
    assert a.run_id == b.run_id and len(runner.llm.calls) == 1
    assert b.experiment_run_id == "exp-1"


def test_material_request_wait_kind(cases, tmp_path):
    ask = tool_call("ask_user", kind="material_request",
                    questions=[{"question_id": "q1", "text": "请上传维修费发票。"}])
    runner = runner_for(cases, tmp_path, [ask, tool_call("finish", answer="ok")])
    info = runner.start_run(CASE, "hi")
    assert info.wait.kind == "material_request"
    info = runner.resume_run(info.run_id, UserReply(file_ids=["file-9"]), wait_id=info.wait.wait_id)
    assert "file-9" in runner.llm.calls[-1]["messages"][-1]["content"]


# ---------------------------------------------------------------- outputs

def test_partial_output_kept_when_model_fails(cases, tmp_path):
    def boom(messages, tools):
        raise TimeoutError("provider timeout")

    runner = runner_for(cases, tmp_path, [
        LLMResponse(content="初步看，对方应承担主要责任。", tool_calls=tool_call("search_statutes", query="q").tool_calls),
        boom,
    ])
    info = runner.start_run(CASE, "hi")
    assert info.status == "failed"
    assert info.final.partial and info.final.completion_reason == "failed"
    assert "初步看" in info.final.answer and info.final.source_refs  # earlier work retained


def test_citations_only_for_returned_sources(cases, tmp_path):
    runner = runner_for(cases, tmp_path, [
        tool_call("search_statutes", query="交强险"),
        tool_call("finish", answer="先由交强险赔偿。", citations=[
            {"source_id": "civil_code_1213", "location": "先由交强险赔偿。"},
            {"source_id": "made_up_law", "location": "先由交强险赔偿。"},
        ]),
    ])
    final = runner.start_run(CASE, "hi").final
    assert [c.source_id for c in final.citations] == ["civil_code_1213"]
    assert any("made_up_law" in i for i in final.open_issues)


def test_run_deadline(cases, tmp_path):
    def slow(messages, tools):
        time.sleep(0.2)
        return tool_call("search_statutes", query="q")

    runner = runner_for(cases, tmp_path, [slow] * 5, default_config=RunConfig(conversation_route="case_work", max_run_seconds=0.3))
    info = runner.start_run(CASE, "hi")
    assert info.status == "budget_exhausted" and info.final.completion_reason == "budget_exhausted"
    assert "时间上限" in info.final.open_issues[0]


# ---------------------------------------------------------------- model interface

def test_run_config_model_params_reach_the_model(cases, tmp_path):
    cfg = RunConfig(conversation_route="case_work", model="deepseek-chat", seed=7, temperature=0.2, max_output_tokens=800)
    runner = runner_for(cases, tmp_path, [tool_call("finish", answer="ok")], default_config=cfg)
    runner.start_run(CASE, "hi")
    call = runner.llm.calls[0]
    assert (call["model"], call["seed"], call["temperature"], call["max_tokens"]) == ("deepseek-chat", 7, 0.2, 800)
    step = runner.get_trace(runner.store.list_checkpoints().__next__().run_id)[0]
    assert step.arguments["model"] == "deepseek-chat"


def test_unknown_tokens_and_cost_stay_null(cases, tmp_path):
    priced = tool_call("search_statutes", query="q").model_copy(update={
        "usage": TokenUsage(prompt_tokens=10, completion_tokens=5, total_tokens=15),
        "cost": CostInfo(amount="0.000020", price_version="2026-11"),
    })
    # Provider reported no completion count and no price is configured.
    unpriced = LLMResponse(content="答", usage=TokenUsage(prompt_tokens=10, completion_tokens=None))
    runner = runner_for(cases, tmp_path, [priced, unpriced])
    info = runner.start_run(CASE, "hi")
    assert info.usage.estimated_cost is None  # second call had no price -> total unknown
    assert info.usage.tokens_complete is False
    first = runner.get_trace(info.run_id)[0]
    assert first.cost.amount == "0.000020" and first.token_usage.total_tokens == 15


# ---------------------------------------------------------------- trace export and manifest

def test_export_trace_pages_and_exposes_full_results(cases, tmp_path):
    runner = runner_for(cases, tmp_path, [ASK, tool_call("search_statutes", query="交强险"),
                                          tool_call("finish", answer="ok")])
    info = runner.start_run(CASE, "hi")

    page = runner.export_trace(info.run_id, cursor=0, limit=1)
    assert len(page.steps) == 1 and page.next_cursor == 1 and not page.complete
    rest = runner.export_trace(info.run_id, cursor=page.next_cursor)
    assert rest.next_cursor is not None and not rest.complete  # still waiting: more may come

    info = runner.resume_run(info.run_id, UserReply(answers={"q1": "是"}), wait_id=info.wait.wait_id)
    steps, cursor = [], 0
    while True:
        page = runner.export_trace(info.run_id, cursor=cursor, limit=3)
        steps += page.steps
        if page.complete:
            break
        cursor = page.next_cursor
    assert [s.step_id for s in steps] == list(range(1, len(steps) + 1))
    assert len({s.event_id for s in steps}) == len(steps)

    tool_step = next(s for s in steps if s.tool_name == "search_statutes")
    assert tool_step.parent_step_id == tool_step.step_id - 1
    full = runner.get_step_result(info.run_id, tool_step.result_ref)
    assert full["arguments"] == {"query": "交强险"}
    assert full["result"]["data"]["matches"][0]["source_ref"]["source_id"] == "civil_code_1213"
    assert "source_refs" not in full["result"]
    model_step = next(s for s in steps if s.step_type == "model_call")
    assert runner.get_step_result(info.run_id, model_step.result_ref)["response"]["finish_reason"]

    with pytest.raises(KeyError):
        runner.get_step_result(info.run_id, "../checkpoint.json")


def test_runtime_manifest_is_stable_and_has_no_secrets(cases, tmp_path):
    a = runner_for(cases, tmp_path, []).get_runtime_manifest()
    b = runner_for(cases, tmp_path, []).get_runtime_manifest()
    assert a == b
    names = [t.name for t in a.tools]
    assert {"ask_user", "finish", "search_statutes", "check_evidence"} <= set(names)
    assert a.model == {"client": "ScriptedLLM", "default_model": "scripted"}
    assert "key" not in a.model_dump_json().lower()


def test_resume_after_cancel_is_rejected(cases, tmp_path):
    runner = runner_for(cases, tmp_path, [ASK])
    info = runner.start_run(CASE, "hi")
    cancelled = runner.cancel_run(info.run_id)
    assert cancelled.final.completion_reason == "cancelled"
    with pytest.raises(RunStateError):
        runner.resume_run(info.run_id, UserReply(text="x"), wait_id=info.wait.wait_id)
