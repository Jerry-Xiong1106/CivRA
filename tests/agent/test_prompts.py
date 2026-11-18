"""Native prompt rendering across domains; no live model assertions."""
import json

import pytest
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langchain_core.prompts import ChatPromptTemplate

from backend.agent.case_port import InMemoryCaseStore
from backend.agent.model.prompts import AGENT_TEMPLATE, ROUTING_TEMPLATE, assemble
from backend.agent.routing import routing_input
from backend.agent.runtime.middleware import limits
from backend.common.schemas import RunConfig


@pytest.mark.parametrize("question", [
    "我被电动车撞了，他伤得更重，怎么处理",
    "朋友借款没有归还，我想追回本金",
    "房东扣着押金不退，我想处理这个纠纷",
    "买到的家具损坏，商家拒绝退款，下一步怎么办",
    "装修没有按合同完成，我想准备交涉材料",
    "我只想了解合同解除的含义",
])
def test_domain_inputs_are_data_in_native_templates(question):
    cases = InMemoryCaseStore()
    snapshot = cases.create("c", {"statements": [{"text": question}]})
    payload = routing_input(question, snapshot)
    rendered = ROUTING_TEMPLATE.invoke({"routing_input": payload}).to_messages()
    assert isinstance(ROUTING_TEMPLATE, ChatPromptTemplate)
    assert json.loads(rendered[-1].content)["user_request"] == question
    history = [HumanMessage(content=question)]
    context = limits(1, 1, RunConfig(), False)
    view = assemble(history, snapshot, route="case_work", runtime_context=context)
    assert view[-1] is history[0]
    assert question in view[-1].content
    assert '"clarification_rounds": 1' in view[-2].content
    assert context["max_clarification_rounds"] == 2


def test_template_preserves_braces_and_native_tool_call_pair():
    snapshot = InMemoryCaseStore().create("c", {"note": "{history} is literal data"})
    human = HumanMessage(content='请核对 {"amount": 3000, "note": "{runtime_context}"}')
    call = AIMessage(content="", id="ai-1", tool_calls=[
        {"id": "tool-1", "name": "check_evidence", "args": {}},
    ])
    reply = ToolMessage(content='{"status":"ok"}', tool_call_id="tool-1", id="result-1")
    view = assemble([human, call, reply], snapshot, route="case_work",
                    runtime_context=limits(0, 0, RunConfig(), True))
    assert isinstance(AGENT_TEMPLATE, ChatPromptTemplate)
    assert view[-3] is human and view[-2] is call and view[-1] is reply
    assert "{history} is literal data" in view[1].content


def test_case_work_asks_to_record_stated_facts_with_canonical_fields():
    from backend.agent.model.prompts import ROUTE_INSTRUCTIONS
    from backend.case.drafting.templates import COMMON_FIELDS, PROFILES

    case_work, consultation = ROUTE_INSTRUCTIONS["case_work"], ROUTE_INSTRUCTIONS["consultation"]
    assert "update_case" in case_work and "statement_refs" in case_work
    assert 'provenance="user_statement"' in case_work and 'confirmation_status="unconfirmed"' in case_work
    # Field names stay in sync with the drafting templates rather than a copied list.
    for name in [*COMMON_FIELDS, *(field for profile in PROFILES for field in profile[3])]:
        assert name + "=" in case_work, name
    assert "repair_amount=" in case_work
    assert "update_case" not in consultation


def test_case_work_fact_guide_reaches_rendered_prompt_literally():
    snapshot = InMemoryCaseStore().create("c", {"statements": [{"message_id": "m1", "text": "事故在10月8日"}]})
    view = assemble([HumanMessage(content="帮我处理")], snapshot, route="case_work",
                    runtime_context=limits(0, 0, RunConfig(), False))
    rendered = "\n".join(str(message.content) for message in view)
    assert '{"amount":"8000","currency":"CNY"}' in rendered
    assert "accident_date=事故日期" in rendered
