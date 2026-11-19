"""Native control tools; middleware applies pause, route-choice and finish actions."""
from langchain.tools import tool
from backend.tools.policy import set_policy
from backend.agent.tasks import TaskIntent

TASK_CHANGE = TaskIntent.model_json_schema()

ROUTES = ["negotiation", "mediation", "litigation"]

@tool(
    'ask_user',
    description='仅对完成本次任务必需的信息提出问题，并暂停等待回答；遵守本次任务的追问上限。一般规则和初步路线比较不收集完整案情，已跳过的问题保留未知。请求材料时kind为material_request，不询问工具可以查到的内容。',
    args_schema={
        'type': 'object', 'additionalProperties': False, 'required': ['questions'],
        'properties': {
            'task_change': TASK_CHANGE,
            'kind': {'enum': ['clarification', 'material_request']},
            'questions': {'type': 'array', 'minItems': 1, 'items': {
                'type': 'object', 'required': ['question_id', 'text'],
                'properties': {'question_id': {'type': 'string'},
                               'text': {'type': 'string', 'minLength': 1},
                               'reason': {'type': 'string'}, 'field': {'type': 'string'},
                               'gap': {'type':'string', 'minLength':1, 'maxLength':200,
                                       'description':'此问题对应的稳定缺口短名，同一事项复用同一名称。'}},
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
    description='交付本次用户要求的结果。入口任务仍是通用目标时，用 task_change 明确实际交付物。用 task_result.gaps 区分用户待答、材料、待核验及外部待办，仅列阻碍当前交付的事项。',
    args_schema={
        'type': 'object', 'additionalProperties': False, 'required': ['answer'],
        'properties': {
            'answer': {'type': 'string', 'minLength': 1},
            'task_change': TASK_CHANGE,
            'task_result': {'type': 'object', 'additionalProperties': False, 'properties': {
                'answer_is_deliverable': {'type': 'boolean', 'description': '文字交付物已写在 answer 中；承诺以后处理不算交付。无需再抄整段正文。'},
                'known_inputs': {'type':'array', 'maxItems':6, 'items':{'type':'string','minLength':1,'maxLength':500},
                                 'description':'当前交付所用的用户原话摘录，保留未知、否定和条件。'},
                'gaps': {'type':'array','maxItems':20,
                         'description':'完整剩余缺口，按影响排序。同一事项复用 label，只写一次。用户现在能答时填 question，可同轮问多个独立问题。已跳过的保留未知并省略问句，已解决或允许留空的事项移除。',
                         'items': {'type':'object','additionalProperties':False,'required':['label','kind'],
                                   'properties': {'label':{'type':'string','minLength':1,'maxLength':200},
                                                  'kind':{'enum':['user_input','material','verification','external']},
                                                  'question':{'type':'string','minLength':1,'maxLength':500,
                                                              'description':'仅 user_input 使用，给用户的具体问法。'}}}},
                'delivered_text': {'type': 'string', 'description': '回答中实际交付的正文原文'},
                'missing': {'type': 'array', 'items': {'type': 'string'}},
                'mediation_inputs': {'type': 'object', 'additionalProperties': False, 'properties': {
                    'payment_history': {'type': 'string', 'description': '用户关于已付或垫付款的原话'},
                    'payment_preference': {'type': 'string', 'description': '用户关于可接受的付款安排的原话'},
                }},
                'items': {'type': 'array', 'items': {'type': 'object', 'required': ['quote','finding'],
                          'properties': {'quote': {'type': 'string'}, 'finding': {'type': 'string'}}}},
            }},
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
