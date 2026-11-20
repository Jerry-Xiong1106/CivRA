"""Mock tools with the planned names, for developing the runtime before members 2 and 3 deliver.

Each returns fixed sample data in the real ToolResult format. Replace one at a
time as the real tools are registered.
"""

from __future__ import annotations

from backend.agent.case_port import InMemoryCaseStore
from backend.common.schemas import ToolContext, ToolResult
from backend.tools.knowledge_tools import to_tool_result
from langchain.tools import tool, ToolRuntime
from langchain_core.tools import BaseTool
from backend.tools.policy import set_policy

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

def create_mock_tools(cases: InMemoryCaseStore, *, statutes: bool = True,
                      business: bool = True, case_updates: bool = True) -> list[BaseTool]:
    """Build native demonstration tools, explicitly selected by the caller."""

    @tool(
        'search_statutes',
        description='检索当前法律库中的完整条文、条号、版本及来源。query可以是问题或明确的法律名称＋条号。只返回候选，不判断依据充分性；不支持案例、日期或法域过滤。明确指定多条时不按top_k截断。',
        args_schema={'type': 'object',
         'properties': {'query': {'type': 'string', 'minLength': 1},
                        'top_k': {'type': 'integer', 'minimum': 1, 'maximum': 20}},
         'required': ['query'],
         'additionalProperties': False},
    )
    def search_statutes(runtime: ToolRuntime[ToolContext], **args) -> dict:
        result = to_tool_result({'status': 'ok', 'query': args['query'], 'results': [CIVIL_CODE_1213], 'warnings': ['模拟检索结果，仅用于开发联调。']})
        result.summary = '模拟检索：' + result.summary
        return result.model_dump(mode='json', exclude_none=True)

    set_policy(search_statutes, owner='member2')

    @tool(
        'check_evidence',
        description='检查当前案件事实与证据是否齐全、是否冲突。返回缺失字段和建议向用户追问的问题；不直接向用户提问。',
        args_schema={'type': 'object',
         'properties': {'claims': {'type': 'array', 'items': {'type': 'string'}}},
         'additionalProperties': False},
    )
    def check_evidence(runtime: ToolRuntime[ToolContext], **args) -> dict:
        ctx = runtime.context
        facts = cases.get_snapshot(ctx.case_id).summary.get('facts', {})
        missing = [{'field': k, 'label': v, 'question': f'请提供{v}。'} for k, v in REQUIRED_FACTS.items() if k not in facts]
        if missing:
            return ToolResult(status='insufficient', summary='缺少：' + '、'.join((m['label'] for m in missing)), data={'missing': missing, 'conflicts': []}).model_dump(mode='json', exclude_none=True)
        return ToolResult(status='ok', summary='必要事实已齐全（mock）。', data={'missing': [], 'conflicts': []}).model_dump(mode='json', exclude_none=True)

    set_policy(check_evidence, owner='member3', stages=['consultation', 'litigation'])

    @tool(
        'update_case',
        description='把用户确认的新事实写入案件记录，案件版本加 1。只写用户明确提供或确认的信息。',
        args_schema={'type': 'object',
         'properties': {'facts': {'type': 'object', 'minProperties': 1}},
         'required': ['facts'],
         'additionalProperties': False},
    )
    def update_case(runtime: ToolRuntime[ToolContext], **args) -> dict:
        ctx = runtime.context
        version = cases.update_facts(ctx.case_id, args['facts'])
        return ToolResult(status='ok', summary=f"已更新 {len(args['facts'])} 项事实。", case_version=version).model_dump(mode='json', exclude_none=True)

    set_policy(update_case, owner='member3', side_effects=True)

    @tool(
        'verify_output',
        description='核验草稿或分析：引用是否存在、版本是否适用、原文是否支持结论、事实和金额是否与案件记录一致。返回发现的问题和修改建议。',
        args_schema={'type': 'object',
         'properties': {'text': {'type': 'string', 'minLength': 1}},
         'required': ['text'],
         'additionalProperties': False},
    )
    def verify_output(runtime: ToolRuntime[ToolContext], **args) -> dict:
        return ToolResult(status='ok', summary='未发现问题（mock）。', data={'issues': []}).model_dump(mode='json', exclude_none=True)

    set_policy(verify_output, owner='member3')

    @tool(
        'draft_document',
        description='根据确认的事实和法院示范文本生成起诉状草稿，未知字段保留为待补充。仅在用户选择诉讼后可用。',
        args_schema={'type': 'object',
         'properties': {'template_id': {'type': 'string'}},
         'required': ['template_id'],
         'additionalProperties': False},
    )
    def draft_document(runtime: ToolRuntime[ToolContext], **args) -> dict:
        return ToolResult(status='ok', summary='已生成起诉状草稿（mock）。', data={'output_id': 'draft_mock_1'}).model_dump(mode='json', exclude_none=True)

    set_policy(draft_document, owner='member3', stages=['litigation'])

    tools = []
    if case_updates:
        tools.append(update_case)
    if statutes:
        tools.append(search_statutes)
    if business:
        tools.extend([check_evidence, verify_output, draft_document])
    return tools
