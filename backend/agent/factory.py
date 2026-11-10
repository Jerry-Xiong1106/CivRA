"""One-call assembly of a runnable Agent: model client, tools, case store and run store.

    setup = build_runner()                      # real model from .env, real search_statutes
    setup.cases.create("case-1", {"facts": {}})
    info = setup.runner.start_run("case-1", "我的车被追尾了……")

The model is configured like member 5's pilot: SILICONFLOW_API_KEY (or
CIVRA_LLM_API_KEY) from the environment or the repo's .env file. Model calls
go through LangChain's ChatOpenAI by default; CIVRA_LLM_BACKEND=native uses
the plain HTTP OpenAICompatibleClient instead. Member 3's
business tools are not delivered yet, so mock stand-ins are registered by
default; pass mock_business_tools=False to give the model only real tools.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from backend.agent.case_port import InMemoryCaseStore
from backend.agent.llm import LLMClient, OpenAICompatibleClient
from backend.agent.runner import AgentRunner
from backend.agent.store import RunStore
from backend.common.schemas import RunConfig
from backend.tools.knowledge_tools import Search, register_knowledge_tools
from backend.tools.mock_tools import build_mock_registry
from backend.tools.registry import ToolRegistry

ROOT = Path(__file__).resolve().parents[2]
SILICONFLOW_BASE_URL = "https://api.siliconflow.cn/v1"
DEFAULT_MODEL = "Qwen/Qwen2.5-7B-Instruct"   # the model member 5's pilot uses
MOCK_BUSINESS_TOOLS = ("check_evidence", "verify_output", "draft_document")


class MissingAPIKey(RuntimeError):
    pass


def load_dotenv(path: Path | str = ROOT / ".env") -> list[str]:
    """Read KEY=VALUE lines into os.environ without overriding variables already set.

    Returns the names that were set (never the values). A missing file is fine.
    """
    path = Path(path)
    if not path.exists():
        return []
    loaded = []
    for raw in path.read_text(encoding="utf-8-sig").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        name, value = line.split("=", 1)
        name = name.removeprefix("export ").strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "'\"":
            value = value[1:-1]
        if name and value and name not in os.environ:
            os.environ[name] = value
            loaded.append(name)
    return loaded


def llm_from_env(model: str | None = None, backend: str | None = None) -> LLMClient:
    """SiliconFlow by default; any CIVRA_LLM_* variable overrides the default.

    backend: "langchain" (default, ChatOpenAI) or "native" (OpenAICompatibleClient);
    falls back to CIVRA_LLM_BACKEND.
    """
    key = os.environ.get("CIVRA_LLM_API_KEY") or os.environ.get("SILICONFLOW_API_KEY")
    if not key:
        raise MissingAPIKey(
            "没有找到 API Key：请在 CivRA 根目录的 .env 中填写 SILICONFLOW_API_KEY=...（该文件不会被提交），"
            "或设置 CIVRA_LLM_API_KEY。"
        )
    _use_certifi_bundle()
    base_url = os.environ.get("CIVRA_LLM_BASE_URL", SILICONFLOW_BASE_URL)
    model = model or os.environ.get("CIVRA_LLM_MODEL", DEFAULT_MODEL)
    prices = {
        "price_in": os.environ.get("CIVRA_LLM_PRICE_IN"),
        "price_out": os.environ.get("CIVRA_LLM_PRICE_OUT"),
        "price_version": os.environ.get("CIVRA_LLM_PRICE_VERSION"),
    }
    backend = backend or os.environ.get("CIVRA_LLM_BACKEND", "langchain")
    if backend == "native":
        return OpenAICompatibleClient(base_url=base_url, api_key=key, model=model, **prices)
    if backend != "langchain":
        raise ValueError(f"CIVRA_LLM_BACKEND must be langchain or native, not {backend!r}")
    from langchain_openai import ChatOpenAI

    from backend.agent.lc_model import LangChainLLMClient
    # max_retries=0: one provider attempt per call, so usage is never double-counted.
    chat = ChatOpenAI(model=model, base_url=base_url, api_key=key, temperature=0, max_retries=0, timeout=120)
    return LangChainLLMClient(chat, **prices)


def _use_certifi_bundle() -> None:
    # Some Python builds on macOS lack system CA certificates; member 5's pilot does the same.
    try:
        import certifi
    except ImportError:
        return
    os.environ.setdefault("SSL_CERT_FILE", certifi.where())


def build_registry(
    cases: InMemoryCaseStore,
    *,
    real_statutes: bool = True,
    statute_search: Search | None = None,
    mock_business_tools: bool = True,
) -> ToolRegistry:
    """update_case always; search_statutes real or mock; member 3's tools as mocks or not at all."""
    registry = build_mock_registry(cases)
    if real_statutes:
        registry.unregister("search_statutes")
        register_knowledge_tools(registry, statute_search)
    if not mock_business_tools:
        for name in MOCK_BUSINESS_TOOLS:
            registry.unregister(name)
    return registry


@dataclass
class AgentSetup:
    runner: AgentRunner
    cases: InMemoryCaseStore
    registry: ToolRegistry


def build_runner(
    *,
    llm: LLMClient | None = None,
    cases: InMemoryCaseStore | None = None,
    store: RunStore | None = None,
    config: RunConfig | None = None,
    real_statutes: bool = True,
    statute_search: Search | None = None,
    mock_business_tools: bool = True,
    dotenv: Path | str | None = ROOT / ".env",
) -> AgentSetup:
    """Everything needed to run the Agent. Pass llm to use a scripted or other client."""
    if dotenv is not None:
        load_dotenv(dotenv)
    cases = cases or InMemoryCaseStore()
    registry = build_registry(cases, real_statutes=real_statutes, statute_search=statute_search,
                              mock_business_tools=mock_business_tools)
    runner = AgentRunner(llm or llm_from_env(config.model if config else None), registry, cases,
                         store or RunStore(), default_config=config)
    return AgentSetup(runner=runner, cases=cases, registry=registry)


def run_config_from_pilot(plan: dict[str, Any]) -> RunConfig:
    """Map member 5's pilot plan (evaluation/configs/*.json) onto RunConfig.

    Model retries are 0 because the client never retries; tool retries come
    from each ToolSpec (default 1), matching the pilot's tool_retries=1.
    """
    budgets = plan.get("budgets", {})
    return RunConfig(
        mode="agent",
        model=plan.get("model"),
        temperature=plan.get("temperature", 0.0),
        seed=plan.get("seed"),
        max_output_tokens=plan.get("max_output_tokens"),
        max_model_calls=budgets.get("max_model_calls"),
        max_tool_calls=budgets.get("max_tool_calls", RunConfig().max_tool_calls),
        max_total_tokens=budgets.get("max_total_tokens"),
        max_run_seconds=budgets.get("max_active_seconds"),
    )
