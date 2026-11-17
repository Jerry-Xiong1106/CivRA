"""Two semantic entry routes; neither represents a case's resolution route."""
import json
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class RouteDecision(BaseModel):
    model_config = ConfigDict(extra="forbid")
    route: Literal["consultation", "case_work"]
    reason: str = Field(min_length=1, max_length=240)


CONSULTATION_TOOLS = frozenset({"search_statutes", "ask_user", "request_route_choice", "finish"})


def tool_allowed(route: str, name: str) -> bool:
    return route == "case_work" or name in CONSULTATION_TOOLS


def routing_input(message: str, snapshot) -> str:
    summary = snapshot.summary
    return json.dumps({
        "user_request": message,
        "case": {"stage": snapshot.stage, "selected_route": summary.get("selected_route"),
                 "dispute_type": summary.get("dispute_type"), "user_goals": summary.get("user_goals", []),
                 "recent_statements": [s.get("text", "")[:1500] for s in summary.get("statements", [])[-3:]]},
    }, ensure_ascii=False)
