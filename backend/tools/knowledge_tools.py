"""Native LangChain statute tool over the shared retrieval service.

Default retrieval is exact-vector with metadata input. Only query/top_k are
model-facing; case/date filtering and separate source lookup are not provided.
"""
from __future__ import annotations

import os
import threading
from typing import Callable, TYPE_CHECKING

from backend.common.schemas import ToolContext, ToolError, ToolResult
from backend.knowledge.retrieval.results import to_search_match
from langchain.tools import tool, ToolRuntime
from langchain_core.tools import BaseTool
from backend.tools.policy import describe_tool, set_policy

if TYPE_CHECKING:
    from backend.knowledge.retrieval.service import RetrievalService

Search = Callable[..., dict]


def to_tool_result(response: dict, *, source_metadata=None, knowledge_version=None) -> ToolResult:
    """Wrap the unchanged search result. Unknown provenance remains null."""
    if response["status"] not in ("ok", "no_results"):
        return ToolResult.fail("INTERNAL", "检索返回了无法识别的状态。")
    reason = response.get("reason")
    warnings = response.get("warnings", [])
    if reason in ("empty_query", "no_searchable_content", "unsupported_exact_query"):
        message = "；".join(warnings) or "查询格式不受支持。"
        return ToolResult(status="error", summary=message, error=ToolError(
            code="INVALID_ARGS", message=message, details={"reason": reason}))
    matches = [to_search_match(hit, source_metadata or {}) for hit in response["results"]]
    summary = (f"找到 {len(matches)} 条候选；候选不代表已有充分法律依据。" if matches
               else f"未找到候选（{reason or 'no_candidates'}）。")
    return ToolResult(
        status="ok" if matches else "empty", summary=summary,
        data={"schema_version": "0.1", "query": response.get("query"), "matches": matches,
              "empty_reason": reason, "warnings": warnings, "knowledge_version": knowledge_version},
    )


def _lazy_service():
    """Initialize one retrieval service per registered tool, on first use."""
    lock = threading.Lock()
    service = None

    def get_service():
        nonlocal service
        with lock:
            if service is None:
                from backend.knowledge.config import DEFAULT_MODE, RetrievalConfig
                from backend.knowledge.retrieval.service import RetrievalService
                mode = os.environ.get("CIVRA_RETRIEVAL_MODE", DEFAULT_MODE)
                service = RetrievalService.from_config(RetrievalConfig.from_mode(mode))
        return service

    return get_service


def create_knowledge_tools(
    search: Search | None = None, *,
    retrieval_service: RetrievalService | None = None,
) -> list[BaseTool]:
    """Build native real search; inject a service or a test callable, never both."""
    from backend.knowledge.vector.embedding import EmbeddingRequestError

    if search is not None and retrieval_service is not None:
        raise ValueError("Pass search or retrieval_service, not both")
    get_service = _lazy_service() if search is None and retrieval_service is None else lambda: retrieval_service

    @tool(
        'search_statutes',
        description='检索当前法律库中的完整条文、条号、版本及来源。query 可用自然语言描述待检索问题，或逐条写明法律名称＋完整条号。避免条号范围或无法确定所属法律的条号。只返回候选，不判断依据充分性；不支持案例、日期或法域过滤。明确指定多条时不按top_k截断。',
        args_schema={
            'type': 'object',
            'properties': {'query': {'type': 'string', 'minLength': 1},
                           'top_k': {'type': 'integer', 'minimum': 1, 'maximum': 20}},
            'required': ['query'], 'additionalProperties': False,
        },
    )
    def search_statutes(runtime: ToolRuntime[ToolContext], **args) -> dict:
        ctx = runtime.context
        try:
            service = get_service()
            if service is not None:
                response = service.search(args['query'], args.get('top_k'), deadline=ctx.deadline)
            else:
                response = search(args['query'], args.get('top_k'))
            result = to_tool_result(
                response, source_metadata=service.source_metadata if service is not None else None,
                knowledge_version=service.knowledge_version if service is not None else None,
            )
        except FileNotFoundError:
            result = ToolResult.fail('UPSTREAM', '检索索引或来源文件不存在；请按当前模式构建索引（scripts/rag/build_vector_index.py 或 build_bm25_index.py）。')
        except TimeoutError:
            result = ToolResult.fail('TIMEOUT', '检索请求超时。', retryable=True)
        except EmbeddingRequestError as exc:
            result = ToolResult(status='error', summary=str(exc), error=ToolError(
                code='UPSTREAM', message=str(exc), retryable=exc.retryable,
                details={'http_status': exc.status_code}))
        except Exception:
            result = ToolResult.fail('INTERNAL', '检索执行失败，请检查服务配置和索引；未返回替代结果。')
        return result.model_dump(mode='json', exclude_none=True)

    set_policy(search_statutes, owner='member2')
    return [search_statutes]


SEARCH_STATUTES = describe_tool(create_knowledge_tools()[0])
