"""Synthetic end-to-end demonstration of the application confirmation boundary."""
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from backend.case import CaseService, SQLiteCaseRepository
from backend.case.intake.updates import record_user_statement  # noqa: E402
from scripts.case.fixtures import load_development_case
from backend.case.models import CaseSnapshot


def run(output):
    output.mkdir(parents=True, exist_ok=False)
    repository = SQLiteCaseRepository(output / "synthetic_cases.sqlite3")
    service = CaseService(repository)
    for kind, fact_field in [("traffic_accident", "accident_details"), ("private_lending", "loan_agreement"), ("sales_contract", "contract_details")]:
        raw = load_development_case().model_dump(mode="json")
        if not raw["synthetic"]:
            raise ValueError("this demo only supports synthetic cases")
        fact = next(f for f in raw["facts"] if f["fact_id"] == "f_claimant")
        fact.update(fact_id="f_demo", field=fact_field, value="双方于2026年1月5日就相关事项形成书面记录。")
        raw.update(case_id="demo_official_" + kind, stage="litigation", selected_route="litigation",
                   dispute_type=kind, facts=[fact], evidence=[], claims=[])
        case = repository.create_case(CaseSnapshot.model_validate(raw))
        context = dict(case_id=case.case_id, request_id="recommend", experiment_run_id=None, deadline_at=None)
        recommended = service.official_forms.recommend(dict(context=context, case_version=case.case_version))
        card = recommended.data["candidates"][0]
        preview = service.official_forms.prepare(dict(context={**context, "request_id": "prepare"},
            case_version=case.case_version, template_id=card["template_id"], template_version=card["version"]))
        if preview.status == "error":
            raise RuntimeError(preview.error.message)
        (output / (kind + "_preview.json")).write_text(preview.model_dump_json(indent=2), encoding="utf-8")
        # This simulated confirmation is confined to synthetic demo data.
        # Production calls this only after the authenticated user clicks Confirm.
        reply = record_user_statement(repository, case_id=case.case_id, expected_case_version=case.case_version,
            request_id="synthetic-confirmation", message_id="demo-confirm", actor_id="demo-user",
            text=preview.data.structured_data["confirmation_text"], created_at=datetime.now(timezone.utc))
        if reply.status == "error":
            raise RuntimeError(reply.error.message)
        result = service.official_forms.confirm_and_export(dict(context={**context, "request_id": "export"},
            case_version=reply.case_version, preview_output_id=preview.data.output_id, confirmation_message_id="demo-confirm"))
        if result.status == "error":
            raise RuntimeError(result.error.message)
        artifact = service.assets.get_artifact(case.case_id, result.data.artifact_id)
        (output / (kind + ".docx")).write_bytes(artifact["content"])
        print(kind, "filled", len(result.data.structured_data["filled_fields"]), "pending", len(result.data.missing_fields))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True, help="new empty demo directory")
    run(parser.parse_args().output)
