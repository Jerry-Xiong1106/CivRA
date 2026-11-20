"""CaseStatePort over the business CaseService."""

from __future__ import annotations

from backend.case.intake.updates import record_user_statement, set_case_stage
from backend.case.models import CaseSnapshot
from backend.case.planning.guide import ROUTES


def runtime_case_view(snapshot: CaseSnapshot) -> dict:
    """The runtime's case shape: identity, version and stage, everything else in summary."""
    data = snapshot.model_dump(mode="json")
    return {
        "case_id": snapshot.case_id,
        "case_version": snapshot.case_version,
        "stage": snapshot.stage,
        "summary": {key: value for key, value in data.items() if key not in {"case_id", "case_version", "stage"}},
    }


class RepositoryCasePort:
    """Application records real user replies; model tool arguments cannot create them."""

    def __init__(self, service):
        self.service = service
        self.repository = service.repository

    def get_snapshot(self, case_id):
        from backend.agent.case_port import CaseSnapshot as RuntimeSnapshot
        case = self.repository.get_case(case_id)
        view = runtime_case_view(case)
        progress = self.service.routes.view(case)
        if progress:
            view["summary"]["route_progress"] = progress
        view["summary"]["recent_uploaded_files"] = self.service.assets.list_file_refs(case_id)
        return RuntimeSnapshot.model_validate(view)

    def select_route(self, case_id, route):
        if route not in ROUTES:
            raise ValueError(f"unknown route: {route}")
        return set_case_stage(self.repository, case_id, route)

    def route_progress(self, case_id):
        return self.service.routes.view(self.repository.get_case(case_id))

    def record_user_reply(self, **statement):
        return record_user_statement(self.repository, **statement)

    def complete_step(self, case_id, step_id, *, message_id, actor_id, quote, key):
        if not message_id:
            raise ValueError("步骤完成必须引用已保存的用户确认")
        return self.service.routes.complete_step(self.repository.get_case(case_id), step_id,
                                                 message_id=message_id, actor_id=actor_id, quote=quote, key=key)
