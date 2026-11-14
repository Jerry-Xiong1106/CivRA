"""Mock tools with the planned names, for developing the runtime before members 2 and 3 deliver.

Each returns fixed sample data in the real ToolResult format. Replace one at a
time as the real tools are registered.
"""

from __future__ import annotations

from backend.agent.case_port import InMemoryCaseStore
from backend.common.schemas import ToolContext, ToolResult, ToolSpec
from backend.tools.knowledge_tools import SEARCH_STATUTES, to_tool_result
from backend.tools.registry import ToolRegistry

CIVIL_CODE_1213 = {
    "provision_id": "civil_code_1213", "law_title": "中华人民共和国民法典",
    "article_no": "第一千二百一十三条", "hierarchy_path": [],
    "text": "机动车发生交通事故造成损害，属于该机动车一方责任的，先由承保机动车强制保险的保险人在强制保险责任限额范围内予以赔偿……",
    "version": {"publication_date": "2020-05-28", "effective_date": "2021-01-01", "expiry_date": None},
    "source": {"url": "https://flk.npc.gov.cn/", "file": None, "page_start": None, "page_end": None},
    "retrieval": {"rank": 1, "score": 0.82, "score_type": "mock", "matched_by": ["mock"]},
}

# Facts check_evidence expects for a traffic accident claim.
REQUIRED_FACTS = {
    "accident_date": "事故发生日期",
    "liability_finding": "交警事故责任认定",
    "insurance": "对方车辆的保险情况（交强险、商业险）",
}

VERIFY_OUTPUT = ToolSpec(
    name="verify_output",
    description="核验草稿或分析：引用是否存在、版本是否适用、原文是否支持结论、事实和金额是否与案件记录一致。返回发现的问题和修改建议。",
    parameters={
        "type": "object",
        "properties": {"text": {"type": "string", "minLength": 1}},
        "required": ["text"],
        "additionalProperties": False,
    },
    owner="member3",
    feedback_loop="retrieve_verify",
)

CHECK_EVIDENCE = ToolSpec(
    name="check_evidence",
    description="检查当前案件事实与证据是否齐全、是否冲突。返回缺失字段和建议向用户追问的问题；不直接向用户提问。",
    parameters={
        "type": "object",
        "properties": {"claims": {"type": "array", "items": {"type": "string"}}},
        "additionalProperties": False,
    },
    owner="member3",
    stages=["consultation", "litigation"],
)

UPDATE_CASE = ToolSpec(
    name="update_case",
    description="把用户确认的新事实写入案件记录，案件版本加 1。只写用户明确提供或确认的信息。",
    parameters={
        "type": "object",
        "properties": {"facts": {"type": "object", "minProperties": 1}},
        "required": ["facts"],
        "additionalProperties": False,
    },
    owner="member3",
    side_effects=True,
)

DRAFT_DOCUMENT = ToolSpec(
    name="draft_document",
    description="根据确认的事实和法院示范文本生成起诉状草稿，未知字段保留为待补充。仅在用户选择诉讼后可用。",
    parameters={
        "type": "object",
        "properties": {"template_id": {"type": "string"}},
        "required": ["template_id"],
        "additionalProperties": False,
    },
    owner="member3",
    stages=["litigation"],
)


def register_mock_tools(reg: ToolRegistry, cases: InMemoryCaseStore, *,
                        statutes: bool = True, business: bool = True, case_updates: bool = True) -> None:
    """Register selected development tools; case updates use the in-memory store."""

    def search_statutes(args: dict, ctx: ToolContext) -> ToolResult:
        result = to_tool_result({
            "status": "ok", "query": args["query"], "results": [CIVIL_CODE_1213],
            "warnings": ["模拟检索结果，仅用于开发联调。"],
        })
        result.summary = "模拟检索：" + result.summary
        return result


    def check_evidence(args: dict, ctx: ToolContext) -> ToolResult:
        facts = cases.get_snapshot(ctx.case_id).summary.get("facts", {})
        missing = [{"field": k, "label": v, "question": f"请提供{v}。"} for k, v in REQUIRED_FACTS.items() if k not in facts]
        if missing:
            return ToolResult(
                status="insufficient",
                summary="缺少：" + "、".join(m["label"] for m in missing),
                data={"missing": missing, "conflicts": []},
            )
        return ToolResult(status="ok", summary="必要事实已齐全（mock）。", data={"missing": [], "conflicts": []})

    def update_case(args: dict, ctx: ToolContext) -> ToolResult:
        version = cases.update_facts(ctx.case_id, args["facts"])
        return ToolResult(status="ok", summary=f"已更新 {len(args['facts'])} 项事实。", case_version=version)

    def verify_output(args: dict, ctx: ToolContext) -> ToolResult:
        return ToolResult(status="ok", summary="未发现问题（mock）。", data={"issues": []})

    def draft_document(args: dict, ctx: ToolContext) -> ToolResult:
        return ToolResult(status="ok", summary="已生成起诉状草稿（mock）。", data={"output_id": "draft_mock_1"})

    if case_updates:
        reg.register(UPDATE_CASE, update_case)
    if statutes:
        reg.register(SEARCH_STATUTES, search_statutes)
    if business:
        reg.register(CHECK_EVIDENCE, check_evidence)
        reg.register(VERIFY_OUTPUT, verify_output)
        reg.register(DRAFT_DOCUMENT, draft_document)


def build_mock_registry(cases: InMemoryCaseStore) -> ToolRegistry:
    """Convenience entry point for offline demos and tests."""
    registry = ToolRegistry()
    register_mock_tools(registry, cases)
    return registry
