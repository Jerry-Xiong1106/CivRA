"""What the runtime needs from the case store.

RepositoryCasePort (case_adapter.py) implements this over the business
CaseService; InMemoryCaseStore is the dependency-free stand-in for tests and demos.
"""

from __future__ import annotations

from copy import deepcopy
from typing import Any, Protocol

from pydantic import BaseModel, Field

from backend.case.planning.guide import DONE, ROUTES, STEP_WORK
from backend.case.planning.routes import TEMPLATES


class CaseSnapshot(BaseModel):
    case_id: str
    case_version: int
    stage: str
    summary: dict[str, Any] = Field(default_factory=dict)  # facts, statements, route_progress, ...


class CaseStatePort(Protocol):
    def get_snapshot(self, case_id: str) -> CaseSnapshot:
        """Case state shown to the model; includes route_progress once a route is selected."""
        ...

    def select_route(self, case_id: str, route: str) -> int:
        """Save the user's route choice; returns the new case_version."""
        ...

    def route_progress(self, case_id: str) -> dict | None:
        """Steps of the selected route with their status and the current step."""
        ...

    def record_user_reply(self, *, case_id, expected_case_version, request_id, message_id,
                          actor_id, text, created_at) -> Any:
        """Save a user statement so later records can quote it."""
        ...

    def complete_step(self, case_id: str, step_id: str, *, message_id: str | None, actor_id: str,
                      quote: str, key: str) -> dict:
        """Record the user's confirmation that a route step is done; returns new progress."""
        ...


class InMemoryCaseStore:
    """Demo/test store: case summaries plus simple route progress, no business services."""

    def __init__(self) -> None:
        self._cases: dict[str, CaseSnapshot] = {}
        self._progress: dict[tuple[str, str], dict[str, str]] = {}

    def create(self, case_id: str, summary: dict[str, Any] | None = None, stage: str = "consultation") -> CaseSnapshot:
        snap = CaseSnapshot(case_id=case_id, case_version=1, stage=stage, summary=summary or {})
        self._cases[case_id] = snap
        return snap.model_copy(deep=True)

    def get_snapshot(self, case_id: str) -> CaseSnapshot:
        if case_id not in self._cases:
            raise KeyError(f"unknown case: {case_id}")
        snap = self._cases[case_id].model_copy(deep=True)
        progress = self.route_progress(case_id)
        if progress:
            snap.summary["route_progress"] = progress
        return snap

    def select_route(self, case_id: str, route: str) -> int:
        if route not in ROUTES:
            raise ValueError(f"unknown route: {route}")
        snap = self._cases[case_id]
        snap.stage = route
        snap.summary["selected_route"] = route
        snap.case_version += 1
        return snap.case_version

    def route_progress(self, case_id: str) -> dict | None:
        route = self._cases[case_id].summary.get("selected_route")
        if route not in TEMPLATES:
            return None
        done = self._progress.get((case_id, route), {})
        steps = []
        for step_id, title, _, _, materials, condition, _ in TEMPLATES[route]:
            deliverable, tools = STEP_WORK[route][step_id]
            steps.append(dict(step_id=step_id, title=title, status=done.get(step_id, "pending"),
                              materials=materials, completion_condition=condition, deliverable=deliverable,
                              tools=tools, note=None, output_refs=[]))
        current = next((s["step_id"] for s in steps if s["status"] not in DONE), None)
        return dict(route=route, current_step=current, steps=steps, progress_version=len(done))

    def record_user_reply(self, **_):
        raise NotImplementedError("the in-memory store does not record user statements")

    def complete_step(self, case_id, step_id, *, message_id, actor_id, quote, key):
        progress = self.route_progress(case_id)
        if progress is None or step_id not in {s["step_id"] for s in progress["steps"]}:
            raise ValueError("该步骤不属于当前路线")
        self._progress.setdefault((case_id, progress["route"]), {})[step_id] = "completed"
        return self.route_progress(case_id)

    def update_facts(self, case_id: str, facts: dict[str, Any]) -> int:
        snap = self._cases[case_id]
        merged = deepcopy(snap.summary.get("facts", {}))
        merged.update(facts)
        snap.summary["facts"] = merged
        snap.case_version += 1
        return snap.case_version
