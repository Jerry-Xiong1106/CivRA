"""Native control tools; middleware applies pause, route-choice and finish actions."""
from langchain.tools import tool
from backend.tools.policy import set_policy

ROUTES = ["negotiation", "mediation", "litigation"]

@tool(
    'ask_user',
    description='仅对完成本次任务必需的信息提出问题，并暂停等待回答；遵守本次任务的追问上限。一般规则和初步路线比较不收集完整案情，已跳过的问题保留未知。请求材料时kind为material_request，不询问工具可以查到的内容。',
    args_schema={
        'type': 'object', 'additionalProperties': False, 'required': ['questions'],
        'properties': {
            'kind': {'enum': ['clarification', 'material_request']},
            'questions': {'type': 'array', 'minItems': 1, 'items': {
                'type': 'object', 'required': ['question_id', 'text'],
                'properties': {'question_id': {'type': 'string'},
                               'text': {'type': 'string', 'minLength': 1},
                               'reason': {'type': 'string'}, 'field': {'type': 'string'}},
            }},
        },
    },
)
def ask_user(**args) -> dict:
    return args

set_policy(ask_user, owner='member4')

@tool(
    'request_route_choice',
    description='用户需要选择处理方式时，展示协商和解、调解、诉讼等选项并暂停等待选择；不要求先收集完整案情或查全法规，未知费用可保留未知。各路线均可选，不预设先后顺序；用户选择后由程序保存路线。',
    args_schema={
        'type': 'object', 'additionalProperties': False, 'required': ['comparison', 'options'],
        'properties': {
            'comparison': {'type': 'string', 'minLength': 1},
            'options': {'type': 'array', 'minItems': 1, 'items': {
                'type': 'object', 'required': ['route', 'label'],
                'properties': {'route': {'enum': ROUTES}, 'label': {'type': 'string'}},
            }},
        },
    },
)
def request_route_choice(**args) -> dict:
    return args

set_policy(
    request_route_choice,
    owner='member4',
    stages=['consultation', 'route_selection', 'negotiation', 'mediation', 'litigation'],
)

@tool(
    'finish',
    description='结束本次运行并交付当前任务的具体结果，不等于案件办结。路线或计划不能替代用户请求的实际准备成果；关键缺口仍需输入且追问额度允许时应使用ask_user。只列当前相关缺口，不声称已执行未执行的动作。',
    args_schema={
        'type': 'object', 'additionalProperties': False, 'required': ['answer'],
        'properties': {
            'answer': {'type': 'string', 'minLength': 1},
            'open_issues': {'type': 'array', 'items': {'type': 'string'}},
            'output_refs': {'type': 'array', 'items': {'type': 'string'}},
            'citations': {
                'description': '回答中依赖来源的句子或段落，以及对应的 source_id（来自工具返回的 data.matches[].source_ref）',
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

set_policy(finish, owner='member4')

BUILTINS = {t.name: t for t in (ask_user, request_route_choice, finish)}
