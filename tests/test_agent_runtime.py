"""Runtime tests with mock tools and a scripted model; no API key or network needed."""

from __future__ import annotations

import json
import time

import pytest

from backend.agent.case_port import InMemoryCaseStore
from backend.agent.llm import ScriptedLLM, tool_call
from backend.agent.runner import AgentRunner, RunConflict, RunStateError
from backend.agent.store import RunStore
from backend.common.schemas import LLMResponse, RunConfig, ToolResult, ToolSpec, UserReply
from backend.tools.mock_tools import build_mock_registry
from backend.tools.registry import ToolRegistry

CASE = "case_demo"


@pytest.fixture
def cases():
    store = InMemoryCaseStore()
    store.create(CASE, {"dispute_type": "traffic_accident", "facts": {"accident_date": "2026-05-12"}})
    return store


def make_runner(cases, tmp_path, script, **kw):
    return AgentRunner(ScriptedLLM(script), build_mock_registry(cases), cases, RunStore(tmp_path), **kw)


def last_tool_result(messages) -> dict:
    return json.loads([m for m in messages if m["role"] == "tool"][-1]["content"])


def test_tool_feedback_leads_to_question_then_resume_after_restart(cases, tmp_path):
    """Acceptance demo: check_evidence reports a gap -> agent asks -> run pauses -> resumes in a new runner."""

    def ask_after_gap(messages, tools):
        result = last_tool_result(messages)
        assert result["status"] == "insufficient"
        return tool_call("ask_user", questions=[
            {"question_id": "q1", "text": "交警是否出具了事故责任认定书？结论是什么？", "field": "liability_finding"},
            {"question_id": "q2", "text": "对方车辆是否投保了交强险和商业险？", "field": "insurance"},
        ])

    def record_answers(messages, tools):
        assert "对方全责" in messages[-1]["content"]
        return tool_call("update_case", facts={"liability_finding": "对方全责", "insurance": "交强险+商业三者险100万"})

    script = [
        tool_call("check_evidence"),
        ask_after_gap,
        # --- user answers; resumed in a fresh runner below
        record_answers,
        tool_call("check_evidence"),
        tool_call("search_statutes", query="机动车交通事故 交强险 赔偿顺序"),
        tool_call("finish", answer="可先向对方交强险保险人主张赔偿……", open_issues=["医疗费票据尚未上传"]),
    ]
    llm = ScriptedLLM(script)
    store = RunStore(tmp_path)
    runner = AgentRunner(llm, build_mock_registry(cases), cases, store)

    info = runner.start_run(CASE, "我的车被追尾了，想要对方赔偿维修费。")
    assert info.status == "waiting_user"
    assert info.wait.kind == "clarification"
    assert [q.question_id for q in info.wait.questions] == ["q1", "q2"]

    # Simulate a process restart: a new runner with the same store continues from the checkpoint.
    runner2 = AgentRunner(llm, build_mock_registry(cases), cases, store)
    info = runner2.resume_run(info.run_id, UserReply(answers={"q1": "对方全责", "q2": "交强险+商业三者险100万"}),
                              wait_id=info.wait.wait_id, expected_case_version=1)

    assert info.status == "completed"
    assert info.final.open_issues == ["医疗费票据尚未上传"]
    assert [s.source_id for s in info.final.source_refs] == ["civil_code_1213"]
    assert info.final.case_version == 2  # update_case bumped the version
    assert info.usage.model_calls == 6 and info.usage.tool_calls == 4

    trace = runner2.get_trace(info.run_id)
    assert [(s.step_type, s.tool_name, s.status) for s in trace if s.step_type != "model_call"] == [
        ("tool_call", "check_evidence", "insufficient"),
        ("ask_user", "ask_user", "waiting"),
        ("tool_call", "update_case", "ok"),
        ("tool_call", "check_evidence", "ok"),
        ("tool_call", "search_statutes", "ok"),
        ("finish", "finish", "ok"),
    ]
    assert [s.step_id for s in trace] == list(range(1, len(trace) + 1))

    types = [e.type for e in runner2.iter_events(info.run_id)]
    assert types[0] == "run.started" and types[-1] == "run.completed"
    assert "run.waiting_user" in types and "run.resumed" in types
    seqs = [e.seq for e in runner2.iter_events(info.run_id)]
    assert seqs == list(range(1, len(seqs) + 1))
    assert all(e.seq > 5 for e in runner2.iter_events(info.run_id, after_seq=5))


def test_model_sees_latest_case_state_each_call(cases, tmp_path):
    seen_versions = []

    def capture(messages, tools):
        seen_versions.append(json.loads(messages[1]["content"].split("\n", 1)[1])["case_version"])
        return tool_call("finish", answer="ok") if len(seen_versions) == 2 else tool_call("update_case", facts={"x": 1})

    runner = make_runner(cases, tmp_path, [capture, capture])
    runner.start_run(CASE, "hi")
    assert seen_versions == [1, 2]


def test_stage_gating_hides_and_rejects_litigation_tools(cases, tmp_path):
    def check_offered(messages, tools):
        names = [t.name for t in tools]
        assert "draft_document" not in names and "check_evidence" in names
        return tool_call("draft_document", template_id="traffic_complaint")

    runner = make_runner(cases, tmp_path, [check_offered, tool_call("finish", answer="done")])
    info = runner.start_run(CASE, "帮我写起诉状")
    step = [s for s in runner.get_trace(info.run_id) if s.tool_name == "draft_document"][0]
    assert step.status == "error" and step.error.code == "NOT_ALLOWED"


def test_route_choice_moves_stage_and_unlocks_tools(cases, tmp_path):
    def check_litigation_tools(messages, tools):
        assert "draft_document" in [t.name for t in tools]
        return tool_call("draft_document", template_id="traffic_complaint")

    script = [
        tool_call("request_route_choice", comparison="协商成本低；诉讼周期长但有强制力。",
                  options=[{"route": "negotiation", "label": "协商和解"}, {"route": "litigation", "label": "诉讼"}]),
        check_litigation_tools,
        tool_call("finish", answer="草稿已生成", output_refs=["draft_mock_1"]),
    ]
    runner = make_runner(cases, tmp_path, script)
    info = runner.start_run(CASE, "我想走法律途径")
    assert info.status == "waiting_user" and info.wait.kind == "route_selection"

    with pytest.raises(RunStateError):
        runner.resume_run(info.run_id, UserReply(route_choice="mediation"), wait_id=info.wait.wait_id)  # not offered

    info = runner.resume_run(info.run_id, UserReply(route_choice="litigation"), wait_id=info.wait.wait_id)
    assert info.status == "completed"
    assert cases.get_snapshot(CASE).stage == "litigation"
    assert info.final.output_refs == ["draft_mock_1"]


def test_invalid_arguments_are_returned_to_model(cases, tmp_path):
    def expect_error(messages, tools):
        result = last_tool_result(messages)
        assert result["error"]["code"] == "INVALID_ARGS"
        return tool_call("finish", answer="已改正")

    runner = make_runner(cases, tmp_path, [tool_call("search_statutes", query=""), expect_error])
    assert runner.start_run(CASE, "查法条").status == "completed"


def test_plain_text_answer_finishes_run(cases, tmp_path):
    runner = make_runner(cases, tmp_path, [LLMResponse(content="这是直接回答。")])
    info = runner.start_run(CASE, "你好")
    assert info.status == "completed" and info.final.answer == "这是直接回答。"


def test_budget_exhausted(cases, tmp_path):
    script = [tool_call("search_statutes", query="q")] * 10
    runner = make_runner(cases, tmp_path, script, default_config=RunConfig(max_tool_calls=2))
    info = runner.start_run(CASE, "查")
    assert info.status == "budget_exhausted"
    assert info.usage.tool_calls == 2 and info.final.open_issues


def test_token_budget(cases, tmp_path):
    # ScriptedLLM reports 120 tokens per call.
    script = [tool_call("search_statutes", query="q")] * 10
    runner = make_runner(cases, tmp_path, script, default_config=RunConfig(max_total_tokens=300))
    info = runner.start_run(CASE, "查")
    assert info.status == "budget_exhausted" and info.usage.model_calls == 3
    assert "token" in info.final.open_issues[0]


def _registry_with_failing_search(cases):
    reg = build_mock_registry(cases)
    reg._tools.pop("search_statutes")
    reg.register(
        ToolSpec(name="search_statutes", description="t", owner="m2", feedback_loop="retrieve_verify",
                 parameters={"type": "object", "properties": {"query": {"type": "string"}}}),
        lambda a, c: ToolResult(status="empty", summary="没有找到"),
    )
    return reg


def test_retrieve_verify_round_limit(cases, tmp_path):
    def expect_blocked(messages, tools):
        result = last_tool_result(messages)
        assert result["error"]["code"] == "NOT_ALLOWED" and "3 轮" in result["error"]["message"]
        return tool_call("finish", answer="依据不足", open_issues=["未找到直接相关条文"])

    script = [tool_call("search_statutes", query="q")] * 3 + [tool_call("search_statutes", query="q4"), expect_blocked]
    runner = AgentRunner(ScriptedLLM(script), _registry_with_failing_search(cases), cases, RunStore(tmp_path))
    info = runner.start_run(CASE, "查")
    assert info.status == "completed"
    assert info.usage.tool_calls == 4  # the 4th was recorded but not executed
    assert any("3 轮" in i for i in info.final.open_issues) and "未找到直接相关条文" in info.final.open_issues


def test_round_limit_resets_after_user_reply(cases, tmp_path):
    script = [tool_call("search_statutes", query="q")] * 3 + [
        tool_call("ask_user", questions=[{"question_id": "q1", "text": "事故在哪个城市？"}]),
        tool_call("search_statutes", query="q"),
        lambda m, t: tool_call("finish", answer="ok") if last_tool_result(m)["status"] == "empty" else None,
    ]
    runner = AgentRunner(ScriptedLLM(script), _registry_with_failing_search(cases), cases, RunStore(tmp_path))
    info = runner.start_run(CASE, "查")
    info = runner.resume_run(info.run_id, UserReply(answers={"q1": "上海"}), wait_id=info.wait.wait_id)
    assert info.status == "completed"
    assert not any("3 轮" in i for i in info.final.open_issues)


def test_stale_flag_after_case_update(cases, tmp_path):
    runner = make_runner(cases, tmp_path, [tool_call("finish", answer="ok")])
    info = runner.start_run(CASE, "hi")
    assert runner.get_run(info.run_id).stale is False
    cases.update_facts(CASE, {"repair_cost": "8000.00"})
    assert runner.get_run(info.run_id).stale is True


def test_long_history_is_compacted_but_stored_in_full(cases, tmp_path):
    big = "很长的条文内容" * 400
    reg = build_mock_registry(cases)
    reg._tools.pop("search_statutes")
    reg.register(
        ToolSpec(name="search_statutes", description="t", owner="m2",
                 parameters={"type": "object", "properties": {"query": {"type": "string"}}}),
        lambda a, c: ToolResult(status="ok", summary="找到条文", data={"text": big}),
    )
    sizes = []

    def measure(messages, tools):
        sizes.append(sum(len(json.dumps(m, ensure_ascii=False)) for m in messages))
        return tool_call("finish", answer="ok") if len(sizes) == 12 else tool_call("search_statutes", query="q")

    cfg = RunConfig(max_context_chars=20_000, max_tool_calls=50, max_steps=60)
    runner = AgentRunner(ScriptedLLM([measure] * 12), reg, cases, RunStore(tmp_path), default_config=cfg)
    info = runner.start_run(CASE, "我的车被追尾了")
    assert info.status == "completed"
    assert max(sizes) <= 20_000

    sent = runner.llm.calls[-1]["messages"]
    assert sent[2] == {"role": "user", "content": "我的车被追尾了"}  # first user message always kept
    ids_called = {c["id"] for m in sent if m.get("tool_calls") for c in m["tool_calls"]}
    ids_answered = {m["tool_call_id"] for m in sent if m["role"] == "tool"}
    assert ids_called == ids_answered  # no orphaned tool_calls after compaction

    from backend.agent.store import RunStore as RS
    stored = RS(tmp_path).load_checkpoint(info.run_id).messages
    assert sum(big in (m.get("content") or "") for m in stored if m["role"] == "tool") == 11  # full history kept


def test_model_error_fails_run(cases, tmp_path):
    def boom(messages, tools):
        raise ConnectionError("network down")

    runner = make_runner(cases, tmp_path, [boom])
    info = runner.start_run(CASE, "hi")
    assert info.status == "failed" and "network down" in info.error


def test_cancel_while_waiting_and_conflicts(cases, tmp_path):
    runner = make_runner(cases, tmp_path, [tool_call("ask_user", questions=[{"question_id": "q1", "text": "?"}])])
    info = runner.start_run(CASE, "hi")
    assert runner.cancel_run(info.run_id).status == "cancelled"
    with pytest.raises(RunStateError):
        runner.resume_run(info.run_id, UserReply(text="late"), wait_id="wait_x")


def test_one_running_run_per_case(cases, tmp_path):
    store = RunStore(tmp_path)
    runner = AgentRunner(ScriptedLLM([]), build_mock_registry(cases), cases, store)
    from backend.agent.store import Checkpoint
    store.save_checkpoint(Checkpoint(run_id="run_x", case_id=CASE, status="running", config=RunConfig(),
                                     messages=[], case_version=1, stage="consultation"))
    with pytest.raises(RunConflict):
        runner.start_run(CASE, "hi")


# ---------------------------------------------------------------- registry

def _ctx():
    from datetime import datetime, timezone
    from backend.common.schemas import ToolContext
    return ToolContext(case_id=CASE, run_id="r", step_id=1, case_version=1,
                       deadline=datetime.now(timezone.utc), stage="consultation")


def _spec(name, **kw):
    return ToolSpec(name=name, description="t", owner="test",
                    parameters={"type": "object", "properties": {}}, **kw)


def test_registry_timeout_retries_then_reports():
    reg = ToolRegistry()
    calls = []

    @reg.tool(_spec("slow", timeout_s=0.05, max_retries=1))
    def slow(args, ctx):
        calls.append(1)
        time.sleep(0.3)
        return ToolResult(status="ok")

    ex = reg.execute("slow", {}, _ctx())
    assert ex.result.error.code == "TIMEOUT" and ex.attempts == 2 and len(calls) == 2


def test_registry_exception_becomes_internal_error_without_retry():
    reg = ToolRegistry()

    @reg.tool(_spec("bad", max_retries=3))
    def bad(args, ctx):
        raise ValueError("oops")

    ex = reg.execute("bad", {}, _ctx())
    assert ex.result.error.code == "INTERNAL" and ex.attempts == 1


def test_registry_rejects_duplicates_and_unknown_tools():
    reg = ToolRegistry()
    reg.register(_spec("a"), lambda a, c: ToolResult(status="ok"))
    with pytest.raises(ValueError):
        reg.register(_spec("a"), lambda a, c: ToolResult(status="ok"))
    assert reg.execute("nope", {}, _ctx()).result.error.code == "NOT_FOUND"
