"""LangChain model client and tool wrappers, against the same fake HTTP provider as the native client."""

from __future__ import annotations

import json

import pytest
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
from langchain_openai import ChatOpenAI

from backend.agent.case_port import InMemoryCaseStore
from backend.agent.factory import build_registry, build_runner
from backend.agent.lc_model import LangChainLLMClient, to_langchain_messages
from backend.agent.store import RunStore
from backend.common.schemas import RunConfig, UserReply
from backend.tools.knowledge_tools import SEARCH_STATUTES
from backend.tools.lc_tools import to_langchain_tools
from evaluation.adapters.team import AgentAdapter
from tests.test_agent_factory import FakeProvider, completion


@pytest.fixture
def provider():
    servers = []

    def make(replies):
        servers.append(FakeProvider(replies))
        return servers[-1]

    yield make
    for s in servers:
        s.close()


def lc_client(url, **prices):
    chat = ChatOpenAI(model="deepseek-chat", base_url=url, api_key="sk-test", temperature=0, max_retries=0, timeout=10)
    return LangChainLLMClient(chat, **prices)


def test_message_conversion_round_trip():
    msgs = to_langchain_messages([
        {"role": "system", "content": "s"},
        {"role": "user", "content": "u"},
        {"role": "assistant", "content": None, "tool_calls": [
            {"id": "c1", "type": "function", "function": {"name": "search_statutes", "arguments": '{"query": "交强险"}'}}]},
        {"role": "tool", "tool_call_id": "c1", "content": '{"status": "ok"}'},
    ])
    assert [type(m) for m in msgs] == [SystemMessage, HumanMessage, AIMessage, ToolMessage]
    assert msgs[2].tool_calls[0]["args"] == {"query": "交强险"} and msgs[3].tool_call_id == "c1"
    with pytest.raises(ValueError):
        to_langchain_messages([{"role": "developer", "content": "x"}])


def test_langchain_client_request_and_response(provider):
    fake = provider([completion(calls=[("search_statutes", {"query": "交强险"})], finish="tool_calls", rid="chatcmpl-1"),
                     completion(content="好的", usage=False)])
    client = lc_client(fake.url, price_in="2", price_out="8", price_version="2026-11")
    r = client.chat([{"role": "user", "content": "hi"}], [SEARCH_STATUTES], model="deepseek-reasoner",
                    temperature=0.3, max_tokens=500, seed=7, tool_choice="search_statutes")
    sent = fake.requests[0]
    assert sent["path"] == "/v1/chat/completions" and sent["auth"] == "Bearer sk-test"
    body = sent["body"]
    assert (body["model"], body["temperature"], body["seed"]) == ("deepseek-reasoner", 0.3, 7)
    assert body.get("max_tokens", body.get("max_completion_tokens")) == 500
    assert body["tools"][0]["function"]["name"] == "search_statutes"
    assert body["tool_choice"]["function"]["name"] == "search_statutes"
    assert r.tool_calls[0].arguments == {"query": "交强险"} and r.tool_calls[0].tool_name == "search_statutes"
    assert r.finish_reason == "tool_calls" and r.provider_request_id == "chatcmpl-1"
    assert (r.usage.prompt_tokens, r.usage.completion_tokens, r.usage.total_tokens, r.usage.cached_tokens) == (120, 30, 150, 20)
    assert r.cost.amount == "0.000480"

    r2 = client.chat([{"role": "user", "content": "hi"}])
    body2 = fake.requests[1]["body"]
    assert body2["model"] == "deepseek-chat" and "tools" not in body2
    assert r2.content == "好的" and r2.cost is None


def test_malformed_tool_arguments_reach_the_registry_as_invalid(provider):
    reply = completion(calls=[("search_statutes", {})], finish="tool_calls")
    reply["choices"][0]["message"]["tool_calls"][0]["function"]["arguments"] = '{"query": "交强'  # broken JSON
    client = lc_client(provider([reply]).url)
    r = client.chat([{"role": "user", "content": "hi"}], [SEARCH_STATUTES])
    assert r.tool_calls[0].tool_name == "search_statutes" and "__unparsed__" in r.tool_calls[0].arguments


def test_full_agent_with_langchain_client_through_member5_adapter(provider, tmp_path):
    from backend.knowledge.retrieval.results import to_result
    provision = {
        "provision_id": "民法典::第一千二百一十三条", "law_title": "中华人民共和国民法典", "article_no": "第一千二百一十三条",
        "hierarchy_path": [], "text": "……先由承保机动车强制保险的保险人……赔偿", "source_url": "u", "source_file": "f",
        "page_start": 1, "page_end": 1, "publication_date": "2020-05-28", "effective_date": "2021-01-01",
        "expiry_date": None, "status": "有效",
    }
    fake = provider([
        completion(calls=[("ask_user", {"questions": [{"question_id": "q1", "text": "对方有交强险吗？"}]})], finish="tool_calls"),
        completion(calls=[("search_statutes", {"query": "交强险"}), ("update_case", {"facts": {"insurance": "有"}})],
                   finish="tool_calls"),
        completion(calls=[("finish", {"answer": "先由交强险赔偿。", "citations": [
            {"source_id": "民法典::第一千二百一十三条", "location": "先由交强险赔偿。"}]})], finish="tool_calls"),
    ])
    setup = build_runner(llm=lc_client(fake.url), store=RunStore(tmp_path), mock_business_tools=False, dotenv=None,
                         statute_search=lambda q, k=None: {"status": "ok", "query": q, "warnings": [],
                                                           "results": [to_result(provision, 1, 9.0, ["keyword"])]})
    setup.cases.create("eval-1", {"facts": {}})
    out = AgentAdapter(setup.runner).run("eval-1", "被追尾了", RunConfig(), "exp-lc",
                                         lambda wait: UserReply(answers={"q1": "有"}))
    run = out["run"]
    assert run["status"] == "completed" and run["final"]["citations"]
    assert run["case_version"] == 2  # update_case ran in the same turn as the parallel search
    third = fake.requests[2]["body"]["messages"]
    # The fake provider reuses ids across replies, so compare lists, not sets.
    called = [c["id"] for m in third if m.get("tool_calls") for c in m["tool_calls"]]
    answered = [m["tool_call_id"] for m in third if m["role"] == "tool"]
    assert len(called) == 3 and called == answered  # LangChain kept every tool_call paired, in order


def test_registered_tools_as_langchain_tools():
    cases = InMemoryCaseStore()
    cases.create("c", {"facts": {}})
    registry = build_registry(cases, statute_search=lambda q, k=None: {
        "status": "no_results", "query": q, "results": [], "reason": "no_keyword_matches", "warnings": ["没有"]})
    tools = {t.name: t for t in to_langchain_tools(registry, cases, "c")}
    assert "draft_document" not in tools  # litigation-only tool is hidden in consultation
    assert tools["search_statutes"].args_schema == SEARCH_STATUTES.parameters

    result = tools["search_statutes"].invoke({"query": "交强险"})
    assert result["status"] == "empty" and result["data"]["reason"] == "no_keyword_matches"
    assert tools["update_case"].invoke({"facts": {"x": 1}})["case_version"] == 2
    bad = tools["search_statutes"].invoke({"query": "x", "dispute_type": "t"})
    assert bad["error"]["code"] == "INVALID_ARGS"  # same validation as inside the Agent
