"""Native prompt rendering; no live model assertions."""
import pytest
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langchain_core.prompts import ChatPromptTemplate

from backend.agent.case_port import InMemoryCaseStore
from backend.agent.model.prompts import AGENT_TEMPLATE, assemble, stage_instructions
from backend.agent.runtime.middleware import limits
from backend.agent.runtime.state import Checkpoint
from backend.common.schemas import RunConfig


def _limits(searches=0, rounds=0):
    cp = Checkpoint(run_id="r", thread_id="t", case_id="c", status="running", config=RunConfig(),
                    case_version=1, stage="consultation", search_calls=searches, clarification_rounds=rounds)
    return limits(cp)


@pytest.mark.parametrize("question", [
    "我被电动车撞了，他伤得更重，怎么处理",
    "朋友借款没有归还，我想追回本金",
    "我只想了解合同解除的含义",
])
def test_user_input_and_runtime_limits_reach_the_prompt(question):
    snapshot = InMemoryCaseStore().create("c", {"statements": [{"text": question}]})
    history = [HumanMessage(content=question)]
    view = assemble(history, snapshot, runtime_context=_limits(1, 1))
    assert view[-1] is history[0]
    assert '"clarification_rounds": 1' in view[-2].content


def test_template_preserves_braces_and_native_tool_call_pair():
    snapshot = InMemoryCaseStore().create("c", {"note": "{history} is literal data"})
    human = HumanMessage(content='请核对 {"amount": 3000, "note": "{runtime_context}"}')
    call = AIMessage(content="", id="ai-1", tool_calls=[{"id": "tool-1", "name": "check_evidence", "args": {}}])
    reply = ToolMessage(content='{"status":"ok"}', tool_call_id="tool-1", id="result-1")
    view = assemble([human, call, reply], snapshot, runtime_context=_limits())
    assert isinstance(AGENT_TEMPLATE, ChatPromptTemplate)
    assert view[-3] is human and view[-2] is call and view[-1] is reply
    assert "{history} is literal data" in view[1].content


def test_consultation_points_the_user_to_the_route_entries():
    text = stage_instructions(None)
    assert "compare_routes" in text and "路线入口" in text
    assert "confirm_step" not in text


def test_route_prompt_guides_the_current_step_and_names_its_id():
    cases = InMemoryCaseStore()
    cases.create("c", {})
    cases.select_route("c", "litigation")
    text = stage_instructions(cases.route_progress("c"))
    assert "诉讼准备" in text and "第 1/4 步" in text and "step_id=jurisdiction" in text
    assert "confirm_step" in text
    cases.complete_step("c", "jurisdiction", message_id=None, actor_id="u", quote="好了", key="k")
    text = stage_instructions(cases.route_progress("c"))
    assert "step_id=evidence" in text and "已完成步骤：核实管辖" in text


def test_finished_route_summarizes_instead_of_guiding():
    cases = InMemoryCaseStore()
    cases.create("c", {})
    cases.select_route("c", "mediation")
    for step in ("channel", "participate", "result"):
        cases.complete_step("c", step, message_id=None, actor_id="u", quote="好了", key=step)
    text = stage_instructions(cases.route_progress("c"))
    assert "所有步骤都已完成" in text and "confirm_step" not in text


def test_fact_recording_uses_canonical_fields_in_every_stage():
    from backend.case.drafting.templates import COMMON_FIELDS, PROFILES

    cases = InMemoryCaseStore()
    cases.create("c", {})
    cases.select_route("c", "negotiation")
    for text in (stage_instructions(None), stage_instructions(cases.route_progress("c"))):
        assert "update_case" in text and "statement_refs" in text
        assert 'provenance="user_statement"' in text and 'confirmation_status="unconfirmed"' in text
        # Field names stay in sync with the drafting templates rather than a copied list.
        for name in [*COMMON_FIELDS, *(field for profile in PROFILES for field in profile[3])]:
            assert name + "=" in text, name
        assert '{"amount":"8000","currency":"CNY"}' in text


def test_case_context_hides_empty_and_bookkeeping_fields():
    from backend.agent.model.prompts import case_context
    statements = [{"message_id": f"m{i}", "text": f"陈述{i}"} for i in range(9)]
    snapshot = InMemoryCaseStore().create("c", {
        "statements": statements, "schema_version": "0.1", "created_at": "x",
        "facts": [{"fact_id": "f1", "field": "accident_date", "value": "2026-10-08", "evidence_ids": [],
                   "derivation_ref": None}]})
    text = case_context(snapshot)
    assert "schema_version" not in text and "created_at" not in text
    assert "evidence_ids" not in text and "derivation_ref" not in text
    assert '"earlier_statements_omitted":3' in text and "陈述8" in text and "陈述2" not in text
