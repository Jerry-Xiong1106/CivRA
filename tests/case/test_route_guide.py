"""Route guidance over the step templates and user-confirmed progress."""
from datetime import datetime, timezone

import pytest

from backend.case import CaseService, SQLiteCaseRepository
from backend.case.intake.updates import record_user_statement, set_case_stage
from backend.case.models import CaseSnapshot
from backend.case.planning.guide import ROUTES, STEP_WORK
from backend.case.planning.routes import TEMPLATES


@pytest.fixture
def service(tmp_path):
    repo = SQLiteCaseRepository(tmp_path / "cases.sqlite3")
    now = datetime.now(timezone.utc)
    repo.create_case(CaseSnapshot(case_id="c1", case_version=1, synthetic=False, dispute_type=None,
                                  jurisdiction="CN-Mainland", stage="consultation", selected_route=None,
                                  created_at=now, updated_at=now))
    return CaseService(repo)


def say(service, text, message_id):
    case = service.repository.get_case("c1")
    result = record_user_statement(service.repository, case_id="c1", expected_case_version=case.case_version,
                                   request_id=message_id, message_id=message_id, actor_id="u", text=text,
                                   created_at=datetime.now(timezone.utc))
    assert result.status == "ok"


def test_every_template_step_has_guidance():
    assert set(ROUTES) == set(TEMPLATES) == set(STEP_WORK)
    for route, steps in TEMPLATES.items():
        assert [step[0] for step in steps] == list(STEP_WORK[route])


def test_no_progress_before_a_route_is_selected(service):
    assert service.routes.view(service.repository.get_case("c1")) is None


@pytest.mark.parametrize("route", ROUTES)
def test_first_step_is_current_after_selection(service, route):
    set_case_stage(service.repository, "c1", route)
    view = service.routes.view(service.repository.get_case("c1"))
    assert view["route"] == route and view["current_step"] == TEMPLATES[route][0][0]
    assert all(step["status"] == "pending" and step["deliverable"] and step["tools"] for step in view["steps"])


def test_completion_quotes_the_user_and_advances(service):
    set_case_stage(service.repository, "c1", "litigation")
    say(service, "管辖法院已经核对好了", "m1")
    view = service.routes.complete_step(service.repository.get_case("c1"), "jurisdiction", message_id="m1",
                                        actor_id="u", quote="管辖法院已经核对好了", key="k1")
    assert view["current_step"] == "evidence"
    assert view["steps"][0]["status"] == "completed" and "管辖法院已经核对好了" in view["steps"][0]["note"]


def test_completion_requires_a_saved_statement(service):
    set_case_stage(service.repository, "c1", "negotiation")
    with pytest.raises(ValueError):
        service.routes.complete_step(service.repository.get_case("c1"), "prepare", message_id="missing",
                                     actor_id="u", quote="好了", key="k")


def test_progress_survives_switching_routes(service):
    set_case_stage(service.repository, "c1", "negotiation")
    say(service, "诉求整理好了", "m1")
    service.routes.complete_step(service.repository.get_case("c1"), "prepare", message_id="m1",
                                 actor_id="u", quote="诉求整理好了", key="k1")
    set_case_stage(service.repository, "c1", "mediation")
    assert service.routes.view(service.repository.get_case("c1"))["current_step"] == "channel"
    set_case_stage(service.repository, "c1", "negotiation")
    assert service.routes.view(service.repository.get_case("c1"))["current_step"] == "communicate"


def test_update_case_records_the_dispute_type(service):
    from backend.case.contracts import InvocationContext
    from backend.case.intake.contracts import CaseChanges, UpdateCaseRequest
    from backend.case.intake.updates import update_case

    def change(dispute_type, request_id):
        case = service.repository.get_case("c1")
        return update_case(UpdateCaseRequest(
            context=InvocationContext(request_id=request_id, case_id="c1", experiment_run_id=None, deadline_at=None),
            expected_case_version=case.case_version, changes=CaseChanges(dispute_type=dispute_type),
            change_reason="案由明确"), service.repository)

    assert change("traffic_accident", "r1").status == "ok"
    assert service.repository.get_case("c1").dispute_type == "traffic_accident"
    assert change("traffic_accident", "r2").status == "error"  # nothing new to save
    with pytest.raises(ValueError):
        CaseChanges(dispute_type="unknown_type")


def test_dispute_types_cover_every_complaint_template():
    from typing import get_args
    from backend.case.drafting.templates import PROFILES
    from backend.case.intake.contracts import DisputeType
    assert {profile[0] for profile in PROFILES} <= set(get_args(DisputeType))
