"""Export actual public request/result examples. No hidden labels or model calls."""

import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from backend.case import CaseService, SQLiteCaseRepository
from backend.case.intake.updates import set_case_stage  # noqa: E402
from scripts.case.fixtures import load_development_case
from backend.case.materials.contracts import BuildEvidenceDirectoryRequest, CheckEvidenceRequest
from backend.case.contracts import InvocationContext
from backend.case.intake.contracts import UpdateCaseRequest


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    destination = ROOT / "data/examples/member3/phase2_calls"
    destination.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory() as directory:
        repo = SQLiteCaseRepository(Path(directory) / "cases.sqlite3")
        snapshot = repo.create_case(load_development_case())
        service = CaseService(repo)

        def context(name):
            return InvocationContext(request_id=name, case_id=snapshot.case_id, experiment_run_id="development-only", deadline_at=None)

        check = CheckEvidenceRequest(context=context("check-example"), case_version=1, case_snapshot=snapshot)
        update = UpdateCaseRequest(context=context("update-example"), expected_case_version=1,
            changes={"upsert_statements": [{"message_id": "msg_example_insurance", "actor_id": "synthetic_user",
                "text": "保险信息暂时不知道。", "created_at": "2026-11-02T08:00:00Z"}],
                "upsert_facts": [{"fact_id": "f_insurance_unknown", "field": "insurance", "value_type": "text", "value": None,
                    "provenance": "user_statement", "confirmation_status": "unconfirmed", "statement_refs": ["msg_example_insurance"]}]},
            change_reason="保存合成用户的明确未知回复，不将未知改成已投保")
        calls = {"check_evidence": (check, service.check_evidence(check)), "update_case": (update, service.update_case(update))}
        set_case_stage(repo, snapshot.case_id, "litigation")
        selected = repo.get_case(snapshot.case_id)
        directory_request = BuildEvidenceDirectoryRequest(context=context("directory-example"),
            case_version=selected.case_version, case_snapshot=selected)
        calls["build_evidence_directory"] = (directory_request, service.build_evidence_directory(directory_request))
        for name, (request, result) in calls.items():
            (destination / f"{name}.json").write_text(json.dumps({"synthetic_development_only": True,
                "request": request.model_dump(mode="json"), "result": result.model_dump(mode="json")},
                ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        invalid = {"check_evidence": {}, "update_case": {"context": context("invalid").model_dump(mode="json"),
            "expected_case_version": 1, "changes": {}, "change_reason": "invalid empty update"},
            "build_evidence_directory": check.model_dump(mode="json")}
        results = {"check_evidence": service.check_evidence(invalid["check_evidence"]),
                   "update_case": service.update_case(invalid["update_case"]),
                   "build_evidence_directory": service.build_evidence_directory(invalid["build_evidence_directory"])}
        (destination / "invalid_requests.json").write_text(json.dumps({name: {"request": invalid[name],
            "result": results[name].model_dump(mode="json")} for name in invalid}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"Exported public request/result examples: {destination}")


if __name__ == "__main__":
    main()
