"""What the runtime needs from the case store.

Case state is defined by member 3 and persisted through member 1's storage API.
The runtime only depends on this small port; InMemoryCaseStore stands in until
the real store exists.
"""

from __future__ import annotations

from copy import deepcopy
from typing import Any, Protocol

from pydantic import BaseModel, Field


class CaseSnapshot(BaseModel):
    case_id: str
    case_version: int
    stage: str
    summary: dict[str, Any] = Field(default_factory=dict)  # facts etc. shown to the model


class CaseStatePort(Protocol):
    def get_snapshot(self, case_id: str) -> CaseSnapshot: ...

    def set_stage(self, case_id: str, stage: str) -> int:
        """Move the case to a user-confirmed stage; returns the new case_version."""
        ...


class InMemoryCaseStore:
    def __init__(self) -> None:
        self._cases: dict[str, CaseSnapshot] = {}

    def create(self, case_id: str, summary: dict[str, Any] | None = None, stage: str = "consultation") -> CaseSnapshot:
        snap = CaseSnapshot(case_id=case_id, case_version=1, stage=stage, summary=summary or {})
        self._cases[case_id] = snap
        return snap.model_copy(deep=True)

    def get_snapshot(self, case_id: str) -> CaseSnapshot:
        if case_id not in self._cases:
            raise KeyError(f"unknown case: {case_id}")
        return self._cases[case_id].model_copy(deep=True)

    def set_stage(self, case_id: str, stage: str) -> int:
        snap = self._cases[case_id]
        snap.stage = stage
        snap.case_version += 1
        return snap.case_version

    def update_facts(self, case_id: str, facts: dict[str, Any]) -> int:
        snap = self._cases[case_id]
        merged = deepcopy(snap.summary.get("facts", {}))
        merged.update(facts)
        snap.summary["facts"] = merged
        snap.case_version += 1
        return snap.case_version
