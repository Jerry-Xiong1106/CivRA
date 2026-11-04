"""search_statutes: adapts member 2's RetrievalService to the tool protocol (contract §2).

Only translation lives here; retrieval logic stays in backend/knowledge. The
index is loaded on first use, so importing this module needs no index file.

The default mode is keyword (exact article lookup, then BM25), which needs no
embedding API key. Set CIVRA_RETRIEVAL_MODE to vector, exact-vector or hybrid
once those indexes and keys are available.
"""

from __future__ import annotations

import os
import threading
from typing import Any, Callable

from backend.common.schemas import SourceRef, ToolContext, ToolResult, ToolSpec
from backend.tools.registry import ToolRegistry

SEARCH_STATUTES = ToolSpec(
    name="search_statutes",
    description=(
        "在法律条文库中检索，返回条文原文、条款编号、版本和出处。query 可以是问题或关键词，"
        "也可以是“民法典第一千二百一十三条”这样的精确条号。只返回候选条文，是否适用由你判断："
        "status 为 ok 只表示找到了候选，不表示依据充分，排名靠后的结果可能无关。"
        "提供 incident_date（事故或纠纷发生日期）时，会标记发生时尚未生效或已失效的条文。"
    ),
    parameters={
        "type": "object",
        "properties": {
            "query": {"type": "string", "minLength": 1},
            "incident_date": {"type": "string", "pattern": r"^\d{4}-\d{2}-\d{2}$"},
            "top_k": {"type": "integer", "minimum": 1, "maximum": 20},
        },
        "required": ["query"],
        "additionalProperties": False,
    },
    owner="member2",
    feedback_loop="retrieve_verify",
)

Search = Callable[..., dict]


def to_tool_result(response: dict, incident_date: str | None = None) -> ToolResult:
    """Translate a RetrievalService response. no_results is a normal outcome, not an error."""
    if response["status"] != "ok":
        reason = response.get("reason")
        warnings = response.get("warnings", [])
        return ToolResult(status="empty", summary="；".join(warnings) or "没有检索到结果。",
                          data={"reason": reason, "query": response.get("query"), "hits": []})

    hits, refs, out_of_force = [], [], []
    for r in response["results"]:
        version, source = r["version"], r["source"]
        warning = _version_warning(version, incident_date)
        hits.append({
            "source_id": r["provision_id"],
            "law_title": r["law_title"],
            "article_no": r["article_no"],
            "hierarchy_path": r["hierarchy_path"],
            "text": r["text"],
            "rank": r["retrieval"]["rank"],
            "score": r["retrieval"]["score"],
            "matched_by": r["retrieval"]["matched_by"],
            "status": version["status"],
            "effective_date": version["effective_date"],
            "expiry_date": version["expiry_date"],
            "page_start": source["page_start"],
            "page_end": source["page_end"],
            "version_warning": warning,
        })
        refs.append(SourceRef(
            source_id=r["provision_id"],
            source_type="statute",
            title=r["law_title"],
            locator=r["article_no"],
            version=version["effective_date"],
            excerpt=r["text"],
            url=source["url"],
        ))
        if warning:
            out_of_force.append(f"{r['law_title']}{r['article_no']}")

    summary = f"找到 {len(hits)} 条候选：" + "、".join(f"{h['law_title']}{h['article_no']}" for h in hits[:3])
    if out_of_force:
        summary += f"。注意：{'、'.join(out_of_force)} 在 {incident_date} 时可能不适用，见 version_warning"
    return ToolResult(status="ok", summary=summary, source_refs=refs,
                      data={"query": response.get("query"), "hits": hits, "warnings": response.get("warnings", [])})


def _version_warning(version: dict[str, Any], incident_date: str | None) -> str | None:
    """ISO dates compare correctly as strings. Whether a transitional rule applies is a legal question."""
    if incident_date is None:
        return None
    effective, expiry = version["effective_date"], version["expiry_date"]
    if effective and incident_date < effective:
        return f"事件发生于 {incident_date}，早于本条生效日 {effective}，需核对过渡规则或当时适用的法律。"
    if expiry and incident_date >= expiry:
        return f"事件发生于 {incident_date}，本条已于 {expiry} 失效。"
    return None


def _default_search() -> Search:
    """Load the retrieval service once, on first call."""
    lock = threading.Lock()
    service: list[Any] = []

    def search(query: str, top_k: int | None = None) -> dict:
        with lock:
            if not service:
                from backend.knowledge.config import RetrievalConfig
                from backend.knowledge.retrieval.service import RetrievalService
                mode = os.environ.get("CIVRA_RETRIEVAL_MODE", "keyword")
                service.append(RetrievalService.from_config(RetrievalConfig.from_mode(mode)))
        return service[0].search(query, top_k)

    return search


def register_knowledge_tools(registry: ToolRegistry, search: Search | None = None) -> None:
    """Register search_statutes. Tests can inject `search` to avoid loading an index."""
    run_search = search or _default_search()

    @registry.tool(SEARCH_STATUTES)
    def search_statutes(args: dict, ctx: ToolContext) -> ToolResult:
        try:
            response = run_search(args["query"], args.get("top_k"))
        except FileNotFoundError as e:
            return ToolResult.fail("UPSTREAM", f"检索索引不存在，请先运行 scripts/rag/build_bm25_index.py：{e}")
        except ValueError as e:
            return ToolResult.fail("UPSTREAM", f"检索配置错误：{e}")
        return to_tool_result(response, args.get("incident_date"))
