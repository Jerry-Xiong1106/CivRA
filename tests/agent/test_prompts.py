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
    assert context["max_clarification_rounds"] == 3


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
