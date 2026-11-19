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

    def __init__(self, repository, assets=None, litigation=None):
        self.repository = repository
        self.assets = assets
        self.litigation = litigation

    def extracted_text(self, case_id, ref):
        from backend.case.models import MaterialRef
        return self.assets.read_extraction(case_id, MaterialRef.model_validate_json(ref)) if self.assets else ""

    def directory_gaps(self, case_id, ref):
        from backend.case.errors import CaseNotFound
        try:
            stored = self.assets.get_output(case_id, ref)
        except CaseNotFound:
            return None
        output = stored["output"]
        if stored["stale"] or output.output_type != "evidence_directory":
            return None
        data = output.structured_data or {}
        gaps = [item["message"] for item in data.get("supplementary_checklist", [])]
        if not data.get("entries"):
            gaps.append("目录暂无已登记的证据条目")
        return gaps

    def draft_missing_fields(self, case_id, output_id, formats):
        """Return saved gaps for a readable, current complaint, or None if invalid."""
        from backend.case.errors import CaseNotFound
        if not self.assets:
            return None
        try:
            stored = self.assets.get_output(case_id, output_id)
            output = stored["output"]
            if stored["stale"] or not (output.template_id or "").startswith("cn_complaint_"):
                return None
            artifacts = (output.structured_data or {}).get("artifacts", {})
            if (output.output_type == "document" and artifacts
                    and all(fmt in artifacts and self.assets.get_artifact(case_id, artifacts[fmt])["content"]
                            for fmt in (formats or list(artifacts)))):
                return output.missing_fields
        except CaseNotFound:
            return None
        return None

    def business_case(self, case_id):
        return self.repository.get_case(case_id)

    def get_snapshot(self, case_id):
        from backend.agent.case_port import CaseSnapshot as RuntimeSnapshot
        case = self.repository.get_case(case_id)
        view = runtime_case_view(case)
        if self.litigation and case.selected_route == "litigation":
            view["summary"]["route_progress"] = self.litigation.view(case)
        if self.assets and hasattr(self.assets, "list_file_refs"):
            view["summary"]["recent_uploaded_files"] = self.assets.list_file_refs(case_id)
        return RuntimeSnapshot.model_validate(view)

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
