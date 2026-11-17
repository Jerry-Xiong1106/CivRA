"""Member 4 CaseStatePort implementation over the shared business repository."""

from __future__ import annotations

from backend.case.intake.contracts import CaseChanges, UpdateCaseRequest
from backend.case.contracts import InvocationContext
from ..case.models import CaseSnapshot, UserStatement
from ..case.intake.updates import set_case_stage, update_case


def runtime_case_view(snapshot: CaseSnapshot) -> dict:
    """Construct Member 4's current CaseSnapshot shape without importing Agent code."""
    data = snapshot.model_dump(mode="json")
    return {
        "case_id": snapshot.case_id,
        "case_version": snapshot.case_version,
        "stage": snapshot.stage,
        "summary": {key: value for key, value in data.items() if key not in {"case_id", "case_version", "stage"}},
    }


class RepositoryCasePort:
    """Application records real user replies; model tool arguments cannot create them."""

    def __init__(self, repository):
        self.repository = repository

    def get_snapshot(self, case_id):
        from backend.agent.case_port import CaseSnapshot as RuntimeSnapshot
        return RuntimeSnapshot.model_validate(runtime_case_view(self.repository.get_case(case_id)))

    def set_stage(self, case_id, stage):
        return set_case_stage(self.repository, case_id, stage)

    def record_user_reply(self, *, case_id, expected_case_version, request_id, message_id, actor_id, text, created_at):
        """Application calls this with authenticated user data BEFORE resume_run.

        The message_id can then be referenced by model-proposed confirmations.
        This method stores the reply; it does not interpret the reply as confirmation.
        created_at is the application's original receipt timestamp, reused on retries.
        """
        return update_case(UpdateCaseRequest(
            context=InvocationContext(request_id=request_id, case_id=case_id, experiment_run_id=None, deadline_at=None),
            expected_case_version=expected_case_version,
            changes=CaseChanges(upsert_statements=[UserStatement(message_id=message_id, actor_id=actor_id,
                text=text, created_at=created_at)]),
            change_reason="application_recorded_user_reply"), self.repository)
