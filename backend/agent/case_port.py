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
    """Everything the runtime reads or writes about a case.

    litigation is the litigation route guide, or None when the store has no
    business services (the in-memory demo store).
    """
    litigation: Any

    def get_snapshot(self, case_id: str) -> CaseSnapshot: ...

    def set_stage(self, case_id: str, stage: str) -> int:
        """Move the case to a user-confirmed stage; returns the new case_version."""
        ...

    def business_case(self, case_id: str) -> Any:
        """The business layer's own case snapshot, for route guidance."""
        ...

    def record_user_reply(self, *, case_id, expected_case_version, request_id, message_id,
                          actor_id, text, created_at) -> Any: ...

    def extracted_text(self, case_id: str, ref: str) -> str: ...

    def directory_gaps(self, case_id: str, ref: str) -> list[str] | None:
        """Gaps of a current evidence directory, or None if the output is not one."""
        ...

    def draft_missing_fields(self, case_id: str, output_id: str, formats) -> list[str] | None:
        """Missing fields of a current, readable complaint, or None if it is not one."""
        ...


class InMemoryCaseStore:
    """Demo/test store: case summaries only, no business outputs or route guidance."""
    litigation = None

    def __init__(self) -> None:
        self._cases: dict[str, CaseSnapshot] = {}

    def business_case(self, case_id: str):
        raise NotImplementedError("the in-memory store has no business case model")

    def record_user_reply(self, **_):
        raise NotImplementedError("the in-memory store does not record user statements")

    def extracted_text(self, case_id: str, ref: str) -> str:
        return ""

    def directory_gaps(self, case_id: str, ref: str):
        return None

    def draft_missing_fields(self, case_id: str, output_id: str, formats):
        return None

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
