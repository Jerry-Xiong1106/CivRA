"""Factory, model client over real HTTP (against a local fake server) and member 5's AgentAdapter."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from backend.common.config import load_dotenv
from evaluation.config import run_config_from_pilot
from backend.agent.factory import (
    DEFAULT_MODEL, SILICONFLOW_BASE_URL, MissingAPIKey, build_runner, llm_from_env,
)
from tests.native_tools import tool_fixture
from backend.agent.case_port import InMemoryCaseStore
from backend.agent.model.llm import ScriptedLLM, make_chat_model, tool_call
from langchain_openai import ChatOpenAI
from backend.agent.runtime.store import RunStore
from backend.common.schemas import RunConfig, UserReply
from backend.knowledge.retrieval.results import to_result
from evaluation.adapters.team import AgentAdapter

ROOT = Path(__file__).resolve().parents[2]
LLM_VARS = ("SILICONFLOW_API_KEY", "CIVRA_LLM_API_KEY", "CIVRA_LLM_BASE_URL", "CIVRA_LLM_MODEL",
            "CIVRA_LLM_PRICE_IN", "CIVRA_LLM_PRICE_OUT", "CIVRA_LLM_PRICE_VERSION")


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    for name in LLM_VARS:
        monkeypatch.delenv(name, raising=False)


# ---------------------------------------------------------------- .env and model config

def test_load_dotenv_sets_only_missing_names(tmp_path, monkeypatch):
    env = tmp_path / ".env"
    env.write_text('# comment\nSILICONFLOW_API_KEY="sk-test"\nexport CIVRA_LLM_MODEL=Qwen/X\nEMPTY=\nBROKEN\n'
                   "CIVRA_LLM_BASE_URL=http://kept\n", encoding="utf-8")
    monkeypatch.setenv("CIVRA_LLM_BASE_URL", "http://already-set")
    loaded = load_dotenv(env)
    assert sorted(loaded) == ["CIVRA_LLM_MODEL", "SILICONFLOW_API_KEY"]
    import os
    assert os.environ["SILICONFLOW_API_KEY"] == "sk-test" and os.environ["CIVRA_LLM_MODEL"] == "Qwen/X"
    assert os.environ["CIVRA_LLM_BASE_URL"] == "http://already-set"
    assert load_dotenv(tmp_path / "missing.env") == []


def test_llm_from_env_defaults_to_langchain_on_siliconflow(monkeypatch):
    monkeypatch.setenv("SILICONFLOW_API_KEY", "sk-team")
    client = llm_from_env()
    assert isinstance(client, ChatOpenAI) and client.model_name == DEFAULT_MODEL
    chat = client
    assert (chat.openai_api_base, chat.max_retries) == (SILICONFLOW_BASE_URL, 0)
    assert chat.openai_api_key.get_secret_value() == "sk-team"


def test_llm_from_env_overrides_and_rejects_removed_backend(monkeypatch):
    monkeypatch.setenv("CIVRA_LLM_API_KEY", "sk-own")
    monkeypatch.setenv("CIVRA_LLM_BASE_URL", "http://other/v1")
    monkeypatch.setenv("CIVRA_LLM_BACKEND", "native")
    with pytest.raises(ValueError, match="native已移除"):
        llm_from_env(model="deepseek-chat")
    monkeypatch.delenv("CIVRA_LLM_BACKEND")
    client = llm_from_env(model="deepseek-chat")
    assert isinstance(client, ChatOpenAI)
    assert (client.openai_api_base, client.model_name) == ("http://other/v1", "deepseek-chat")
    assert client.openai_api_key.get_secret_value() == "sk-own"


def test_missing_key_is_a_clear_error(tmp_path):
    with pytest.raises(MissingAPIKey, match="SILICONFLOW_API_KEY"):
        build_runner(config=RunConfig(conversation_route="case_work"), dotenv=tmp_path / "none.env", store=RunStore(tmp_path))


def test_registry_options(tmp_path):
    cases = InMemoryCaseStore()
    full = tool_fixture(cases, statute_search=lambda q, k=None: {})
    assert {"search_statutes", "update_case", "check_evidence", "verify_output", "draft_document"} == set(full.names())
    assert full.get_spec("search_statutes").owner == "member2"
    assert set(full.get_spec("search_statutes").parameters["properties"]) == {"query", "top_k"}
    real_only = tool_fixture(cases, statute_search=lambda q, k=None: {}, mock_business_tools=False)
    assert set(real_only.names()) == {"search_statutes", "update_case"}
    mock = tool_fixture(cases, real_statutes=False)
    assert "dispute_type" not in mock.get_spec("search_statutes").parameters["properties"]


def test_pilot_config_maps_to_run_config():
    plan = json.loads((ROOT / "evaluation/configs/pilot_v1.json").read_text(encoding="utf-8"))
    cfg = run_config_from_pilot(plan)
    assert cfg.model == plan["model"] and cfg.max_output_tokens == plan["max_output_tokens"]
    b = plan["budgets"]
    assert (cfg.max_model_calls, cfg.max_tool_calls, cfg.max_total_tokens, cfg.max_run_seconds) == (
        b["max_model_calls"], b["max_tool_calls"], b["max_total_tokens"], b["max_active_seconds"])


def test_max_model_calls_budget(tmp_path):
    setup = build_runner(llm=ScriptedLLM([tool_call("search_statutes", query="q")] * 5), store=RunStore(tmp_path),
                         real_statutes=False, config=RunConfig(conversation_route="case_work", max_model_calls=2), dotenv=None)
    setup.cases.create("c", {})
    info = setup.runner.start_run("c", "hi")
    assert info.status == "budget_exhausted" and info.usage.model_calls == 2
    assert "模型调用上限" in info.final.open_issues[0]


# ---------------------------------------------------------------- real HTTP client against a fake provider

class FakeProvider:
    """Minimal /chat/completions server: records requests, replies from a script."""

    def __init__(self, replies: list[dict]):
        self.replies, self.requests = list(replies), []
        provider = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                provider.requests.append({"path": self.path, "auth": self.headers.get("Authorization"), "body": body})
                payload = json.dumps(provider.replies.pop(0)).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)

            def log_message(self, *args):
                pass

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.server.server_port}/v1"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def close(self):
        self.server.shutdown()


def completion(content=None, calls=(), finish="stop", usage=True, rid="req-1"):
    msg = {"role": "assistant", "content": content}
    if calls:
        msg["tool_calls"] = [{"id": f"call_{i}", "type": "function",
                              "function": {"name": n, "arguments": json.dumps(a, ensure_ascii=False)}}
                             for i, (n, a) in enumerate(calls)]
    reply = {"id": rid, "model": "Qwen/Qwen2.5-7B-Instruct", "choices": [{"message": msg, "finish_reason": finish}]}
    if usage:
        reply["usage"] = {"prompt_tokens": 120, "completion_tokens": 30, "total_tokens": 150,
                          "prompt_tokens_details": {"cached_tokens": 20}}
    return reply


@pytest.fixture
def provider():
    servers = []

    def make(replies):
        servers.append(FakeProvider(replies))
        return servers[-1]

    yield make
    for s in servers:
        s.close()


def test_full_agent_over_http_through_member5_adapter(provider, tmp_path):
    """Real client + factory + member 5's AgentAdapter: ask, disclose, search, cite, finish."""
    provision = {
        "provision_id": "民法典::第一千二百一十三条", "law_title": "中华人民共和国民法典", "article_no": "第一千二百一十三条",
        "hierarchy_path": [], "text": "机动车发生交通事故造成损害……先由承保机动车强制保险的保险人……赔偿",
        "source_url": "https://flk.npc.gov.cn/", "source_file": "民法典.pdf", "page_start": 1, "page_end": 1,
        "publication_date": "2020-05-28", "effective_date": "2021-01-01", "expiry_date": None, "status": "有效",
    }
    fake = provider([
        completion(calls=[("ask_user", {"questions": [{"question_id": "q1", "text": "对方是否投保交强险？"}]})], finish="tool_calls"),
        completion(calls=[("search_statutes", {"query": "交强险 赔偿"})], finish="tool_calls"),
        completion(calls=[("finish", {"answer": "先由交强险赔偿。", "citations": [
            {"source_id": "民法典::第一千二百一十三条", "location": "先由交强险赔偿。"}]})], finish="tool_calls"),
    ])
    client = make_chat_model(base_url=fake.url, api_key="sk-test", model="Qwen/Qwen2.5-7B-Instruct")
    setup = build_runner(config=RunConfig(conversation_route="case_work"), llm=client, store=RunStore(tmp_path), mock_business_tools=False, dotenv=None,
                         statute_search=lambda q, k=None: {"status": "ok", "query": q, "warnings": [],
                                                           "results": [to_result(provision, 1, 9.0, ["keyword"])]})
    setup.cases.create("eval-case-1", {"facts": {}})

    disclosed = []

    def reply_to_wait(wait):
        disclosed.append(wait.questions[0].text)
        return UserReply(answers={"q1": "有交强险"})

    out = AgentAdapter(setup.runner).run("eval-case-1", "我的车被追尾了", RunConfig(conversation_route="case_work", max_output_tokens=800),
                                         "exp-001", reply_to_wait)
    run = out["run"]
    assert run["status"] == "completed" and run["experiment_run_id"] == "exp-001"
    assert run["final"]["citations"][0]["source_id"] == "民法典::第一千二百一十三条" and not run["final"]["open_issues"]
    assert disclosed == ["对方是否投保交强险？"] and len(out["disclosures"]) == 1
    assert [s["step_id"] for s in out["trace"]] == list(range(1, len(out["trace"]) + 1))
    assert out["manifest"]["model"]["default_model"] == "Qwen/Qwen2.5-7B-Instruct"

    # What actually went over the wire on the second call: the disclosed answer and real tool schemas.
    second = fake.requests[1]["body"]
    assert any("有交强险" in (m.get("content") or "") for m in second["messages"])
    assert {t["function"]["name"] for t in second["tools"]} == {"search_statutes", "update_case", "ask_user",
                                                                "request_route_choice", "finish"}
    assert second.get("max_tokens", second.get("max_completion_tokens")) == 800
    # Every assistant tool_call got its tool reply before the next request (OpenAI format requirement).
    third = fake.requests[2]["body"]["messages"]
    called = {c["id"] for m in third if m.get("tool_calls") for c in m["tool_calls"]}
    answered = {m["tool_call_id"] for m in third if m["role"] == "tool"}
    assert called == answered


# ---------------------------------------------------------------- the CLI

def test_run_agent_dry_run_cli(tmp_path):
    proc = subprocess.run(
        [sys.executable, "-W", "ignore", "scripts/agent/run_agent.py", "--dry-run", "--mock-statutes",
         "--message", "我的车被追尾了", "--auto-reply", "有交强险"],
        cwd=ROOT, capture_output=True, text=True, timeout=60,
        env={**{k: v for k, v in os.environ.items() if k.upper() in {"PATH", "SYSTEMROOT", "WINDIR", "TEMP", "TMP"}},
             "CIVRA_RUNS_DIR": str(tmp_path)},
    )
    assert proc.returncode == 0, proc.stderr
    out = proc.stdout
    assert "Agent 追问" in out and "状态：completed" in out and "search_statutes" in out
    assert out.count("#2 ") == 1  # each step printed once, also after resume
