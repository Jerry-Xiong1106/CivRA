"""One-call assembly of a runnable Agent: native tools, case store and run store.

    setup = build_runner()                      # real model from .env, real search_statutes
    setup.cases.create("case-1", {"facts": {}})
    info = setup.runner.start_run("case-1", "我的车被追尾了……")

The model is configured like member 5's pilot: SILICONFLOW_API_KEY (or
CIVRA_LLM_API_KEY) from the environment or the repo's .env file. Model calls
use LangChain ChatOpenAI and create_agent. Pass case_service to register member
3's real tools and use its persistent case repository.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from backend.agent.case_port import CaseStatePort, InMemoryCaseStore
from langchain_core.language_models import BaseChatModel

from backend.agent.model.llm import make_chat_model
from backend.agent.runner import AgentRunner
from backend.agent.runtime.store import RunStore
from backend.common.config import ROOT, load_dotenv
from backend.common.schemas import RunConfig
from backend.tools.knowledge_tools import Search, create_knowledge_tools
from backend.tools.mock_tools import create_mock_tools
from langchain_core.tools import BaseTool

if TYPE_CHECKING:
    from backend.case import CaseService
    from backend.knowledge.retrieval.service import RetrievalService

SILICONFLOW_BASE_URL = "https://api.siliconflow.cn/v1"
DEFAULT_MODEL = "Qwen/Qwen2.5-7B-Instruct"   # the model member 5's pilot uses


class MissingAPIKey(RuntimeError):
    pass


def llm_from_env(model: str | None = None) -> BaseChatModel:
    """One model interface for create_agent and the evaluation baselines."""
    if os.environ.get("CIVRA_LLM_BACKEND", "langchain") != "langchain":
        raise ValueError("CIVRA_LLM_BACKEND=native已移除，请删除该配置并使用LangChain。")
    key = os.environ.get("CIVRA_LLM_API_KEY") or os.environ.get("SILICONFLOW_API_KEY")
    if not key:
        raise MissingAPIKey(
            "没有找到 API Key：请在根目录.env中填写SILICONFLOW_API_KEY，或设置CIVRA_LLM_API_KEY。")
    _use_certifi_bundle()
    return make_chat_model(
        model=model or os.environ.get("CIVRA_LLM_MODEL", DEFAULT_MODEL),
        base_url=os.environ.get("CIVRA_LLM_BASE_URL", SILICONFLOW_BASE_URL), api_key=key,
        price_in=os.environ.get("CIVRA_LLM_PRICE_IN"), price_out=os.environ.get("CIVRA_LLM_PRICE_OUT"),
        price_version=os.environ.get("CIVRA_LLM_PRICE_VERSION"),
    )


def _use_certifi_bundle() -> None:
    # Some Python builds on macOS lack system CA certificates; member 5's pilot does the same.
    try:
        import certifi
    except ImportError:
        return
    os.environ.setdefault("SSL_CERT_FILE", certifi.where())


def build_tools(
    cases: CaseStatePort,
    *,
    real_statutes: bool = True,
    statute_search: Search | None = None,
    retrieval_service: RetrievalService | None = None,
    mock_business_tools: bool = True,
    case_service: CaseService | None = None,
) -> list[BaseTool]:
    """Assemble native tools, selecting persistent or demonstration business services."""
    tools = create_mock_tools(cases, statutes=not real_statutes,
                              business=mock_business_tools and case_service is None,
                              case_updates=case_service is None)
    if real_statutes:
        tools.extend(create_knowledge_tools(statute_search, retrieval_service=retrieval_service))
    if case_service is not None:
        from backend.tools.case_tools import create_case_tools
        tools.extend(create_case_tools(case_service))
    return tools


@dataclass
class AgentSetup:
    runner: AgentRunner
    cases: CaseStatePort
    tools: list[BaseTool]


def build_runner(
    *,
    llm: BaseChatModel | None = None,
    cases: CaseStatePort | None = None,
    store: RunStore | None = None,
    config: RunConfig | None = None,
    real_statutes: bool = True,
    statute_search: Search | None = None,
    retrieval_service: RetrievalService | None = None,
    mock_business_tools: bool = True,
    case_service: CaseService | None = None,
    dotenv: Path | str | None = ROOT / ".env",
) -> AgentSetup:
    """Everything needed to run the Agent. Pass llm to use a scripted or other client."""
    if dotenv is not None:
        load_dotenv(dotenv)
    if cases is None:
        if case_service is None:
            cases = InMemoryCaseStore()
        else:
            from backend.agent.case_adapter import RepositoryCasePort

            cases = RepositoryCasePort(case_service.repository)
    tools = build_tools(cases, real_statutes=real_statutes, statute_search=statute_search,
                        retrieval_service=retrieval_service,
                        mock_business_tools=mock_business_tools, case_service=case_service)
    runner = AgentRunner(llm or llm_from_env(config.model if config else None), tools, cases,
                         store or RunStore(), default_config=config,
                         record_user_messages=case_service is not None)
    return AgentSetup(runner=runner, cases=cases, tools=tools)
