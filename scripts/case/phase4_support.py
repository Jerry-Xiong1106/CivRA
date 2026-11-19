"""Synthetic development fixtures and explicit checks for Member 3 handoff."""

import json
from datetime import datetime, timezone
from pathlib import Path

from backend.case import CaseService, SQLiteCaseRepository
from backend.tools.case_client import CaseToolClient
from backend.tools.case_contracts import ToolInvocation
from scripts.case.fixtures import load_development_case
from backend.case.contracts import InvocationContext
from backend.case.intake.contracts import UpdateCaseRequest
from backend.case.models import BusinessOutput, CaseSnapshot, OutputLink, SourcePointer
from backend.agent.case_adapter import RepositoryCasePort
from demo_support import DEMO_REPLY, confirmed_demo_changes

ROOT = Path(__file__).resolve().parents[2]
SCENARIOS = ROOT / "data/examples/member3/phase4/scenarios.json"


def load_scenarios():
    return json.loads(SCENARIOS.read_text(encoding="utf-8"))["scenarios"]


def prepare_scenario(scenario, directory):
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    repo = SQLiteCaseRepository(directory / "cases.sqlite3")
    payload = load_development_case().model_dump(mode="json")
    payload["case_id"] = scenario["scenario_id"]
    seed = scenario.get("seed", "unresolved")
    if seed == "unknown_dates":
        for fact in payload["facts"]:
            if fact["value_type"] == "date":
                fact["value"] = None
    case = repo.create_case(CaseSnapshot.model_validate(payload))
    service = CaseService(repo)
    port = RepositoryCasePort(repo)
    if seed in {"resolved", "resolved_litigation"}:
        reply = port.record_user_reply(
            case_id=case.case_id, expected_case_version=case.case_version,
            request_id="setup-reply", message_id="msg_demo_reply", actor_id="synthetic_user",
            text=DEMO_REPLY, created_at=datetime(2026, 11, 2, 8, tzinfo=timezone.utc),
        )
        if reply.status != "ok":
            raise AssertionError(reply.summary)
        current = repo.get_case(case.case_id)
        result = service.update_case(UpdateCaseRequest(
            context=InvocationContext(request_id="setup-confirm", case_id=case.case_id,
                                      experiment_run_id="phase4-development", deadline_at=None),
            expected_case_version=current.case_version,
            changes=confirmed_demo_changes(current), change_reason="explicit fixed synthetic reply",
        ))
        if result.status != "ok":
            raise AssertionError(result.summary)
    if seed in {"litigation", "resolved_litigation"}:
        port.set_stage(case.case_id, "litigation")

    if scenario["tool"] == "parse_documents":
        content = scenario.get("material_text", "事故日期：2026-05-12\n维修金额：150.75元\n")
        service.assets.put_file(
            case_id=case.case_id, file_id="phase4_file", file_version="1", file_name="synthetic.txt",
            media_type="text/plain", document_type="other", content=content.encode("utf-8"),
        )
    if scenario["tool"] == "verify_output":
        current = repo.get_case(case.case_id)
        sources = []
        if scenario.get("candidate") == "unavailable_source":
            sources = [SourcePointer(source_id="synthetic_unavailable_source", source_version="v1")]
        service.assets.save_output(BusinessOutput(
            output_id="phase4_candidate", output_version=1, case_id=case.case_id,
            case_version=current.case_version, output_type="analysis", text="合成接口检查样例",
            links=[OutputLink(output_location="claimant", fact_ids=["f_claimant"])], source_refs=sources,
        ))
        if scenario.get("candidate") == "stale":
            port.set_stage(case.case_id, "route_selection")
    current = repo.get_case(case.case_id)
    version = scenario.get("call_version", current.case_version)
    arguments = _resolve_arguments(scenario["arguments"], version)
    invocation = ToolInvocation(
        context=InvocationContext(request_id=scenario["scenario_id"] + "-call", case_id=case.case_id,
                                  experiment_run_id="phase4-development", deadline_at=None),
        case_version=version, arguments=arguments,
    )
    return service, CaseToolClient(service), invocation


def _resolve_arguments(value, version):
    if isinstance(value, dict):
        return {key: _resolve_arguments(item, version) for key, item in value.items()}
    if isinstance(value, list):
        return [_resolve_arguments(item, version) for item in value]
    return version if value == "$case_version" else value


def assert_expected(envelope, expected):
    business = envelope["data"]
    if expected["status"] == "error":
        assert envelope["status"] == "error", envelope
        assert envelope["error"]["code"] == expected["error_code"], envelope
        return
    assert envelope["status"] == "ok", envelope
    assert business["status"] == expected["status"], business
    for check in expected.get("checks", []):
        value = business
        for part in check["path"].split("/")[1:]:
            part = part.replace("~1", "/").replace("~0", "~")
            value = value[int(part)] if isinstance(value, list) else value[part]
        if "equals" in check:
            assert value == check["equals"], (check, value)
        elif "contains" in check:
            assert check["contains"] in value, (check, value)
        elif "length" in check:
            assert len(value) == check["length"], (check, value)
        else:
            raise ValueError("unsupported expectation check")
