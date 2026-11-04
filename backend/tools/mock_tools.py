"""Mock tools with the planned names, for developing the runtime before members 2 and 3 deliver.

Each returns fixed sample data in the real ToolResult format. Replace one at a
time as the real tools are registered.
"""

from __future__ import annotations

from backend.agent.case_port import InMemoryCaseStore
from backend.common.schemas import SourceRef, ToolContext, ToolResult, ToolSpec
from backend.tools.registry import ToolRegistry

CIVIL_CODE_1213 = SourceRef(
    source_id="civil_code_1213",
    source_type="statute",
    title="中华人民共和国民法典",
    locator="第一千二百一十三条",
    version="2021-01-01",
    excerpt="机动车发生交通事故造成损害，属于该机动车一方责任的，先由承保机动车强制保险的保险人在强制保险责任限额范围内予以赔偿……",
    url="https://flk.npc.gov.cn/",
)

# Facts check_evidence expects for a traffic accident claim.
REQUIRED_FACTS = {
    "accident_date": "事故发生日期",
    "liability_finding": "交警事故责任认定",
    "insurance": "对方车辆的保险情况（交强险、商业险）",
}

SEARCH_STATUTES = ToolSpec(
    name="search_statutes",
    description="检索法律、司法解释条文。输入问题和纠纷类型，返回相关条款原文、版本和出处。只返回法条，不做法律结论。",
    parameters={
        "type": "object",
        "properties": {
            "query": {"type": "string", "minLength": 1},
            "dispute_type": {"type": "string"},
            "incident_date": {"type": "string"},
            "top_k": {"type": "integer", "minimum": 1, "maximum": 20},
        },
        "required": ["query"],
        "additionalProperties": False,
    },
    owner="member2",
    feedback_loop="retrieve_verify",
)

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


def build_mock_registry(cases: InMemoryCaseStore) -> ToolRegistry:
    reg = ToolRegistry()

    @reg.tool(SEARCH_STATUTES)
    def search_statutes(args: dict, ctx: ToolContext) -> ToolResult:
        return ToolResult(
            status="ok",
            summary="找到 1 条相关条文（mock）。",
            data={"hits": [{"source_id": CIVIL_CODE_1213.source_id, "score": 0.82}]},
            source_refs=[CIVIL_CODE_1213],
        )

    @reg.tool(CHECK_EVIDENCE)
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

    @reg.tool(UPDATE_CASE)
    def update_case(args: dict, ctx: ToolContext) -> ToolResult:
        version = cases.update_facts(ctx.case_id, args["facts"])
        return ToolResult(status="ok", summary=f"已更新 {len(args['facts'])} 项事实。", case_version=version)

    @reg.tool(VERIFY_OUTPUT)
    def verify_output(args: dict, ctx: ToolContext) -> ToolResult:
        return ToolResult(status="ok", summary="未发现问题（mock）。", data={"issues": []})

    @reg.tool(DRAFT_DOCUMENT)
    def draft_document(args: dict, ctx: ToolContext) -> ToolResult:
        return ToolResult(status="ok", summary="已生成起诉状草稿（mock）。", data={"output_id": "draft_mock_1"})

    return reg
