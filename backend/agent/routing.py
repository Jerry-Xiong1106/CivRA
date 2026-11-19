"""Two semantic entry routes; neither represents a case's resolution route."""
import json
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from backend.agent.tasks import TaskIntent


class RouteDecision(BaseModel):
    model_config = ConfigDict(extra="forbid")
    route: Literal["consultation", "case_work"]
    task: TaskIntent | None = None
    reason: str = Field(min_length=1, max_length=240)


CONSULTATION_TOOLS = frozenset({"search_statutes", "find_form_template", "ask_user", "request_route_choice", "finish"})


def tool_allowed(route: str, name: str) -> bool:
    return route == "case_work" or name in CONSULTATION_TOOLS


def routing_input(message: str, snapshot, tasks=()) -> str:
    summary = snapshot.summary
    return json.dumps({
        "user_request": message, "tasks": [t.model_dump(mode="json") for t in tasks],
        "case": {"stage": snapshot.stage, "selected_route": summary.get("selected_route"),
                 "route_progress": summary.get("route_progress"),
                 "dispute_type": summary.get("dispute_type"), "user_goals": summary.get("user_goals", []),
                 "recent_uploaded_files": summary.get("recent_uploaded_files", []),
                 "recent_statements": [s.get("text", "")[:1500] for s in summary.get("statements", [])[-3:]]},
    }, ensure_ascii=False)
