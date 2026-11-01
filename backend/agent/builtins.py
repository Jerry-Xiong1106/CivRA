"""Control actions handled by the runtime itself, offered to the model as tools (contract §2.7)."""

from __future__ import annotations

from backend.common.schemas import ToolSpec

ROUTES = ["negotiation", "mediation", "litigation"]

ASK_USER = ToolSpec(
    name="ask_user",
    description="向用户提出一个或多个具体问题，并暂停运行等待回答。用于补充影响后续分析的关键事实，或请用户确认存在冲突的信息。不要用来询问可以通过工具查到的内容。",
    parameters={
        "type": "object",
        "properties": {
            "questions": {
                "type": "array",
                "minItems": 1,
                "items": {
                    "type": "object",
                    "properties": {
                        "id": {"type": "string"},
                        "text": {"type": "string", "minLength": 1},
                        "reason": {"type": "string"},
                        "field": {"type": "string"},
                    },
                    "required": ["id", "text"],
                },
            }
        },
        "required": ["questions"],
        "additionalProperties": False,
    },
    owner="member4",
)

REQUEST_ROUTE_CHOICE = ToolSpec(
    name="request_route_choice",
    description="完成法律咨询后，向用户展示协商和解、调解、诉讼等路线的比较，并暂停运行等待用户选择。各路线均为可选，不预设先后顺序。",
    parameters={
        "type": "object",
        "properties": {
            "comparison": {"type": "string", "minLength": 1},
            "options": {
                "type": "array",
                "minItems": 1,
                "items": {
                    "type": "object",
                    "properties": {"route": {"enum": ROUTES}, "label": {"type": "string"}},
                    "required": ["route", "label"],
                },
            },
        },
        "required": ["comparison", "options"],
        "additionalProperties": False,
    },
    owner="member4",
    stages=["consultation", "route_selection", "negotiation", "mediation", "litigation"],
)

FINISH = ToolSpec(
    name="finish",
    description="结束本次运行，给出最终回答。列出仍未解决或需要用户确认的事项；没有依据的内容不要写成确定结论。",
    parameters={
        "type": "object",
        "properties": {
            "answer": {"type": "string", "minLength": 1},
            "open_issues": {"type": "array", "items": {"type": "string"}},
            "output_refs": {"type": "array", "items": {"type": "string"}},
        },
        "required": ["answer"],
        "additionalProperties": False,
    },
    owner="member4",
)

BUILTINS: dict[str, ToolSpec] = {s.name: s for s in (ASK_USER, REQUEST_ROUTE_CHOICE, FINISH)}
