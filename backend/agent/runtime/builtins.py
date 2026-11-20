"""Native control tools. They only return their arguments; middleware applies the action."""
from langchain.tools import tool

from backend.common.schemas import ALL_STAGES
from backend.tools.policy import set_policy

ROUTE_STAGES = ["negotiation", "mediation", "litigation"]


@tool(
    'ask_user',
    description='向用户追问推进当前事项必需、且工具查不到的信息，并暂停等待回答。一次只问最关键的问题；'
                '已知或已跳过的不要重问。请求上传材料时 kind 用 material_request。',
    args_schema={
        'type': 'object', 'additionalProperties': False, 'required': ['questions'],
        'properties': {
            'kind': {'enum': ['clarification', 'material_request']},
            'questions': {'type': 'array', 'minItems': 1, 'items': {
                'type': 'object', 'additionalProperties': False, 'required': ['question_id', 'text'],
                'properties': {'question_id': {'type': 'string', 'minLength': 1},
                               'text': {'type': 'string', 'minLength': 1},
                               'reason': {'type': 'string'},
                               'field': {'type': 'string', 'description': '答案对应的事实字段名（如有）'}},
            }},
        },
    },
)
def ask_user(**args) -> dict:
    return args


set_policy(ask_user, owner='member4', stages=list(ALL_STAGES))


@tool(
    'confirm_step',
    description='当前路线步骤的交付物已在本轮回答中给出，或这一步依赖用户在现实中完成的事（如与对方沟通、'
                '提交材料）时，请用户确认这一步是否完成，并暂停等待。step_id 必须是当前步骤；summary '
                '用一两句话概括本步结果。不要替用户确认，不要跳过当前步骤。',
    args_schema={
        'type': 'object', 'additionalProperties': False, 'required': ['step_id', 'summary', 'answer'],
        'properties': {
            'step_id': {'type': 'string', 'minLength': 1},
            'summary': {'type': 'string', 'minLength': 1, 'maxLength': 500},
            'answer': {'type': 'string', 'minLength': 1, 'description': '给用户看的本步完整回答（交付物正文）'},
        },
    },
)
def confirm_step(**args) -> dict:
    return args


set_policy(confirm_step, owner='member4', stages=ROUTE_STAGES)


@tool(
    'finish',
    description='交付本轮回答并结束运行。处于路线步骤中、要请用户确认本步时改用 confirm_step。'
                'citations 只引用工具实际返回的来源；open_issues 只列与当前事项相关的未解决问题。',
    args_schema={
        'type': 'object', 'additionalProperties': False, 'required': ['answer'],
        'properties': {
            'answer': {'type': 'string', 'minLength': 1},
            'open_issues': {'type': 'array', 'items': {'type': 'string'}},
            'output_refs': {'type': 'array', 'items': {'type': 'string'}},
            'citations': {
                'description': '回答中依赖来源的句子或段落，及对应 source_id（来自工具返回的 data.matches[].source_ref）',
                'type': 'array', 'items': {
                    'type': 'object', 'required': ['source_id', 'location'],
                    'properties': {'source_id': {'type': 'string'}, 'location': {'type': 'string'}},
                },
            },
        },
    },
)
def finish(**args) -> dict:
    return args


set_policy(finish, owner='member4', stages=list(ALL_STAGES))

BUILTINS = {t.name: t for t in (ask_user, confirm_step, finish)}
