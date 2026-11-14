"""Member 4 CaseStatePort implementation over the shared business repository."""

from __future__ import annotations

from datetime import datetime, timezone

from .adapters import runtime_case_view
from .contracts import CaseAuditRecord, CaseChanges, InvocationContext, UpdateCaseRequest
from .models import CaseSnapshot, UserStatement
from .updates import update_case


class RepositoryCasePort:
    """Application records real user replies; model tool arguments cannot create them."""

    def __init__(self, repository):
        self.repository = repository

    def get_snapshot(self, case_id):
        from backend.agent.case_port import CaseSnapshot as RuntimeSnapshot
        return RuntimeSnapshot.model_validate(runtime_case_view(self.repository.get_case(case_id)))

    def set_stage(self, case_id, stage):
        """Call only from Member 4's user-route action, never a business tool."""
        current = self.repository.get_case(case_id)
        payload = current.model_dump(mode="json")
        payload.update(stage=stage, selected_route=stage if stage in ("negotiation", "mediation", "litigation") else None,
                       case_version=current.case_version + 1,
                       updated_at=max(datetime.now(timezone.utc), current.updated_at).isoformat())
        updated = CaseSnapshot.model_validate(payload)
        saved = self.repository.replace_case(updated, expected_case_version=current.case_version,
            audit=CaseAuditRecord(case_id=case_id, case_version=updated.case_version, request_id=None,
                change_reason="runtime_user_route_selection", saved_at=datetime.now(timezone.utc)))
        return saved.case_version

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
