"""create_agent lifecycle, tool ordering and local HTTP model integration."""

from __future__ import annotations

import json
import threading
import time

import pytest
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage, convert_to_messages

from backend.agent.case_port import InMemoryCaseStore
from tests.native_tools import tool_fixture
from backend.agent.factory import build_runner
from backend.agent.model.llm import ScriptedLLM, make_chat_model, response_from_message, tool_call
from backend.agent.runner import AgentRunner, RunStateError
from backend.agent.runtime.store import Checkpoint, RunStore
from backend.common.schemas import LLMResponse, RunConfig, ToolResult, ToolSpec, UserReply, Wait, WaitQuestion
from tests.native_tools import NativeTools
from backend.tools.knowledge_tools import SEARCH_STATUTES
from evaluation.adapters.team import AgentAdapter
from tests.agent.test_agent_factory import FakeProvider, completion


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
    return make_chat_model(model="deepseek-chat", base_url=url, api_key="sk-test", timeout_s=10, **prices)


def test_message_conversion_round_trip():
    msgs = convert_to_messages([
        {"role": "system", "content": "s"},
        {"role": "user", "content": "u"},
        {"role": "assistant", "content": None, "tool_calls": [
            {"id": "c1", "type": "function", "function": {"name": "search_statutes", "arguments": '{"query": "交强险"}'}}]},
        {"role": "tool", "tool_call_id": "c1", "content": '{"status": "ok"}'},
    ])
    assert [type(m) for m in msgs] == [SystemMessage, HumanMessage, AIMessage, ToolMessage]
    assert msgs[2].tool_calls[0]["args"] == {"query": "交强险"} and msgs[3].tool_call_id == "c1"
    with pytest.raises(ValueError):
        convert_to_messages([{"role": "unsupported_role", "content": "x"}])


def test_langchain_client_request_and_response(provider):
    fake = provider([completion(calls=[("search_statutes", {"query": "交强险"})], finish="tool_calls", rid="chatcmpl-1"),
                     completion(content="好的", usage=False)])
    client = lc_client(fake.url, price_in="2", price_out="8", price_version="2026-11")
    schema = {"type": "function", "function": {"name": SEARCH_STATUTES.name,
              "description": SEARCH_STATUTES.description, "parameters": SEARCH_STATUTES.parameters}}
    reply = client.bind_tools([schema], model="deepseek-reasoner", temperature=0.3,
                              max_tokens=500, seed=7, tool_choice="search_statutes").invoke([HumanMessage("hi")])
    r = response_from_message(reply, client)
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
    assert r.cost.amount == "0.000480" and r.cost.price_version == "2026-11"

    r2 = response_from_message(client.invoke([HumanMessage("hi")]), client)
    body2 = fake.requests[1]["body"]
    assert body2["model"] == "deepseek-chat" and "tools" not in body2
    assert r2.content == "好的" and r2.cost is None and r2.usage.prompt_tokens is None


def test_malformed_tool_arguments_reach_the_registry_as_invalid(provider):
    reply = completion(calls=[("search_statutes", {})], finish="tool_calls")
    reply["choices"][0]["message"]["tool_calls"][0]["function"]["arguments"] = '{"query": "交强'  # broken JSON
    fake = provider([reply, completion(calls=[("finish", {"answer": "corrected"})], finish="tool_calls")])
    setup = build_runner(config=RunConfig(conversation_route="case_work"), llm=lc_client(fake.url), real_statutes=False, dotenv=None)
    setup.cases.create("malformed", {})
    with __import__("tempfile").TemporaryDirectory() as tmp:
        setup.runner.store = RunStore(tmp)
        info = setup.runner.start_run("malformed", "hi")
        assert info.status == "completed"
        step = next(s for s in setup.runner.get_trace(info.run_id) if s.tool_name == "search_statutes")
        assert step.error.code == "INVALID_ARGS"


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
    setup = build_runner(config=RunConfig(conversation_route="case_work"), llm=lc_client(fake.url), store=RunStore(tmp_path), mock_business_tools=False, dotenv=None,
                         statute_search=lambda q, k=None: {"status": "ok", "query": q, "warnings": [],
                                                           "results": [to_result(provision, 1, 9.0, ["keyword"])]})
    setup.cases.create("eval-1", {"facts": {}})
    out = AgentAdapter(setup.runner).run("eval-1", "被追尾了", RunConfig(conversation_route="case_work", ), "exp-lc",
                                         lambda wait: UserReply(answers={"q1": "有"}))
    run = out["run"]
    assert run["status"] == "completed" and run["final"]["citations"]
    assert run["case_version"] == 2  # update_case ran in the same turn as the parallel search
    third = fake.requests[2]["body"]["messages"]
    # The fake provider reuses ids across replies, so compare lists, not sets.
    called = [c["id"] for m in third if m.get("tool_calls") for c in m["tool_calls"]]
    answered = [m["tool_call_id"] for m in third if m["role"] == "tool"]
    assert len(called) == 3 and called == answered  # LangChain kept every tool_call paired, in order


def test_native_tools_execute_through_create_agent(tmp_path):
    from langchain_core.tools import BaseTool
    cases = InMemoryCaseStore()
    cases.create("c", {"facts": {}})
    registry = tool_fixture(cases, statute_search=lambda q, k=None: {
        "status": "no_results", "query": q, "results": [], "reason": "no_keyword_matches", "warnings": ["没有"]})

    def check_empty(messages, tools):
        assert all(isinstance(t, BaseTool) for t in tools)
        assert "draft_document" not in [t.name for t in tools]
        result = json.loads(messages[-1]["content"])
        assert result["status"] == "empty" and result["data"]["empty_reason"] == "no_keyword_matches"
        return tool_call("update_case", facts={"x": 1})

    model = ScriptedLLM([tool_call("search_statutes", query="q"), check_empty, tool_call("finish", answer="done")])
    runner = AgentRunner(model, registry, cases, RunStore(tmp_path), default_config=RunConfig(conversation_route="case_work"))
    assert runner.start_run("c", "hi").status == "completed"
    assert cases.get_snapshot("c").case_version == 2


def test_pause_skips_later_side_effect_and_resumes_new_runner(tmp_path):
    cases = InMemoryCaseStore()
    cases.create("c", {})
    ask = tool_call("ask_user", questions=[{"question_id": "q1", "text": "confirm?"}])
    update = tool_call("update_case", facts={"unconfirmed": "must not write"})
    model = ScriptedLLM([LLMResponse(tool_calls=ask.tool_calls + update.tool_calls)])
    store = RunStore(tmp_path)
    first = AgentRunner(model, tool_fixture(cases, real_statutes=False), cases, store, default_config=RunConfig(conversation_route="case_work"))
    info = first.start_run("c", "hi")
    assert info.status == "waiting_user" and cases.get_snapshot("c").case_version == 1
    assert (tmp_path / "agent.sqlite3").exists()
    # A previous release may have stored a readable mirror. Resume must ignore
    # that copy and use the durable framework history, even if they disagree.
    path = tmp_path / info.run_id / "checkpoint.json"
    metadata = json.loads(path.read_text(encoding="utf-8"))
    metadata["messages"] = [{"role": "user", "content": "stale mirror must not be replayed"}]
    path.write_text(json.dumps(metadata), encoding="utf-8")
    second_model = ScriptedLLM([tool_call("finish", answer="done")])
    second = AgentRunner(second_model, tool_fixture(cases, real_statutes=False), cases, RunStore(tmp_path), default_config=RunConfig(conversation_route="case_work"))
    info = second.resume_run(info.run_id, UserReply(text="yes"), wait_id=info.wait.wait_id)
    assert info.status == "completed" and len(second_model.calls) == 1
    assert not any("stale mirror" in m.get("content", "") for m in second_model.calls[0]["messages"])
    assert "messages" not in json.loads(path.read_text(encoding="utf-8"))
    assert cases.get_snapshot("c").case_version == 1


def test_finish_skips_later_side_effect(tmp_path):
    cases = InMemoryCaseStore()
    cases.create("c", {})
    finish = tool_call("finish", answer="done")
    update = tool_call("update_case", facts={"late": "must not write"})
    model = ScriptedLLM([LLMResponse(tool_calls=finish.tool_calls + update.tool_calls)])
    runner = AgentRunner(model, tool_fixture(cases, real_statutes=False), cases, RunStore(tmp_path), default_config=RunConfig(conversation_route="case_work"))
    assert runner.start_run("c", "hi").status == "completed"
    assert cases.get_snapshot("c").case_version == 1 and len(model.calls) == 1


def test_old_format_is_not_loaded_or_resumed(tmp_path):
    cases = InMemoryCaseStore()
    cases.create("c", {})
    store = RunStore(tmp_path)
    store.save_checkpoint(Checkpoint(run_id="old", engine="obsolete", case_id="c", status="waiting_user",
        config=RunConfig(conversation_route="case_work", ), messages=[], case_version=1, stage="consultation",
        wait=Wait(wait_id="old-wait", kind="clarification", questions=[WaitQuestion(question_id="q", text="?")])))
    runner = AgentRunner(ScriptedLLM([tool_call("finish", answer="new")]),
                         tool_fixture(cases, real_statutes=False), cases, store, default_config=RunConfig(conversation_route="case_work"))
    with pytest.raises(ValueError, match="格式"):
        runner.get_run("old")
    with pytest.raises(RunStateError, match="格式"):
        runner.resume_run("old", UserReply(text="yes"), wait_id="old-wait")
    assert runner.start_run("c", "new run").status == "completed"


def test_incompatible_source_records_do_not_block_new_runs(tmp_path):
    cases = InMemoryCaseStore()
    cases.create("c", {})
    record = Checkpoint(run_id="old", engine="langchain-create-agent-v1", case_id="c",
                        status="running", config=RunConfig(conversation_route="case_work", ), messages=[], case_version=1,
                        stage="consultation").model_dump()
    record["source_refs"] = [{"source_id": "old", "source_type": "statute", "title": "law",
                              "locator": "第一条", "version": "2021-01-01", "excerpt": "old text"}]
    path = tmp_path / "old" / "checkpoint.json"
    path.parent.mkdir()
    original = json.dumps(record)
    path.write_text(original, encoding="utf-8")
    store = RunStore(tmp_path)
    assert list(store.list_checkpoints()) == []
    runner = AgentRunner(ScriptedLLM([tool_call("finish", answer="new")]),
                         tool_fixture(cases, real_statutes=False), cases, store, default_config=RunConfig(conversation_route="case_work"))
    assert runner.start_run("c", "hi").status == "completed"
    assert path.read_text(encoding="utf-8") == original


def test_missing_framework_checkpoint_does_not_restart_tools(tmp_path):
    cases = InMemoryCaseStore()
    cases.create("c", {})
    store = RunStore(tmp_path)
    runner = AgentRunner(ScriptedLLM([tool_call("ask_user", questions=[{"question_id": "q", "text": "?"}])]),
                         tool_fixture(cases, real_statutes=False), cases, store, default_config=RunConfig(conversation_route="case_work"))
    info = runner.start_run("c", "hi")
    (tmp_path / "agent.sqlite3").unlink()
    model = ScriptedLLM([tool_call("finish", answer="must not run")])
    runner = AgentRunner(model, tool_fixture(cases, real_statutes=False), cases, store, default_config=RunConfig(conversation_route="case_work"))
    info = runner.resume_run(info.run_id, UserReply(text="yes"), wait_id=info.wait.wait_id)
    assert info.status == "failed" and model.calls == []


def test_same_batch_tools_preserve_order_with_duplicate_provider_ids(tmp_path):
    cases = InMemoryCaseStore()
    cases.create("c", {})
    registry, observed = NativeTools(), []
    def first(args, ctx):
        time.sleep(0.02)
        observed.append("first")
        return ToolResult(status="ok")
    def second(args, ctx):
        observed.append("second")
        return ToolResult(status="ok")
    schema = {"type": "object", "properties": {}, "additionalProperties": False}
    registry.register(ToolSpec(name="first", description="first", owner="test", parameters=schema), first)
    registry.register(ToolSpec(name="second", description="second", owner="test", parameters=schema), second)
    a, b = tool_call("first"), tool_call("second")
    b.tool_calls[0].id = a.tool_calls[0].id  # malformed IDs cannot deadlock ordered dispatch
    model = ScriptedLLM([LLMResponse(tool_calls=a.tool_calls + b.tool_calls), tool_call("finish", answer="done")])
    runner = AgentRunner(model, registry, cases, RunStore(tmp_path), default_config=RunConfig(conversation_route="case_work"))
    assert runner.start_run("c", "hi").status == "completed"
    assert observed == ["first", "second"]


def test_cancel_during_model_prevents_following_side_effect(tmp_path):
    cases = InMemoryCaseStore()
    cases.create("c", {})
    entered, release = threading.Event(), threading.Event()
    def slow(messages, tools):
        entered.set()
        assert release.wait(10)
        return tool_call("update_case", facts={"late": "must not write"})
    runner = AgentRunner(ScriptedLLM([slow]), tool_fixture(cases, real_statutes=False), cases, RunStore(tmp_path), default_config=RunConfig(conversation_route="case_work"))
    result = {}
    thread = threading.Thread(target=lambda: result.update(info=runner.start_run("c", "hi")), daemon=True)
    thread.start()
    try:
        assert entered.wait(5)
        cp = next(runner.store.list_checkpoints())
        # Another runner must be able to cancel the active worker, even if the
        # worker subsequently saves its old checkpoint snapshot.
        other = AgentRunner(ScriptedLLM([]), runner.tools, cases, RunStore(tmp_path), default_config=RunConfig(conversation_route="case_work"))
        other.cancel_run(cp.run_id)
        runner.store.save_checkpoint(cp)
    finally:
        release.set()
        thread.join(10)
    assert not thread.is_alive() and result["info"].status == "cancelled"
    assert cases.get_snapshot("c").case_version == 1


def test_prompt_view_preserves_native_message_metadata():
    from backend.agent.model.prompts import assemble
    cases = InMemoryCaseStore()
    snapshot = cases.create("c", {})
    message = AIMessage(content="answer", id="provider-id", additional_kwargs={"reasoning": "opaque"})
    for limit in (None, 10000):
        view = assemble([message], snapshot, limit)
        assert view[-1] is message
        assert view[-1].additional_kwargs == {"reasoning": "opaque"}


def test_compacted_tool_message_preserves_identity_and_metadata():
    from backend.agent.model.prompts import assemble
    cases = InMemoryCaseStore()
    snapshot = cases.create("c", {})
    original = ToolMessage(content=json.dumps({"status": "ok", "summary": "found", "data": "x" * 10000}),
                           id="tool-message-id", tool_call_id="old-call", name="search",
                           additional_kwargs={"provider_tag": "retained"})
    history = [HumanMessage(content="question"),
               AIMessage(content="", tool_calls=[{"id": "old-call", "name": "search", "args": {}}]), original,
               *[HumanMessage(content=f"recent-{i}") for i in range(4)]]
    compacted = next(m for m in assemble(history, snapshot, 3000) if isinstance(m, ToolMessage))
    assert len(compacted.content) < len(original.content)
    assert compacted.id == original.id and compacted.tool_call_id == original.tool_call_id
    assert compacted.additional_kwargs == original.additional_kwargs
    assert len(json.loads(original.content)["data"]) == 10000
