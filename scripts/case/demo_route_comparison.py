"""Isolated synthetic demo: fee replay, source binding, case evidence and persistence."""

import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from backend.case import CaseService, SQLiteCaseRepository, load_development_case
from backend.case.fee_rules import PROPERTY_FEE_RULE_VERSION


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    output = args.output.resolve()
    if output.exists() and any(output.iterdir()):
        raise SystemExit("output must be a new or empty directory")
    output.mkdir(parents=True, exist_ok=True)
    repository = SQLiteCaseRepository(output / "cases.db")
    snapshot = repository.create_case(load_development_case())
    service = CaseService(repository)
    context = dict(request_id="demo-route-comparison-v2", case_id=snapshot.case_id,
                   experiment_run_id="synthetic-feature06", deadline_at=None)
    fee = service.calculate_costs(dict(context=context, case_version=snapshot.case_version,
        calculation_type="property_case_acceptance_fee", rule_version=PROPERTY_FEE_RULE_VERSION,
        inputs=dict(claim_value=dict(amount="3000.00", currency="CNY"), case_category="ordinary_property",
                    procedure="first_instance_ordinary", adjustment="none", filing_date="2026-11-09")))
    assert fee.status == "ok", fee.error
    pointer = service.calculator.fee_rule.pointer
    source = service.get_source(pointer)
    request = dict(context=context, case_version=snapshot.case_version, case_snapshot=snapshot,
        available_authorities=[pointer], calculations=[fee.data, fee.data], user_goals=["节省成本", "尽快解决"],
        source_bindings=[dict(binding_id="fee-article13", route="litigation", dimension="costs",
            statement="普通财产案件受理费基础估算需按指定金额、程序及日期核对。",
            source_ref=pointer, quote=source["text"], conditions=["仅核对本地短引文；完整适用条件仍需核实"])])
    result = service.compare_routes(request)
    assert result.status == "insufficient", result.error
    litigation = next(item for item in result.data.options if item.route == "litigation")
    checks = {
        "three_parallel_routes": len(result.data.options) == 3,
        "five_dimensions_each": all(len(item.dimensions) == 5 for item in result.data.options),
        "case_conflicts_and_duplicates_visible": {"conflict", "duplicate"}.issubset(
            {item.category for item in result.data.case_context.findings}),
        "fee_not_double_counted": len(litigation.costs) == 1 and litigation.costs[0].decimal == 50,
        "loss_amount_not_route_cost": all(value.decimal != 3000 for item in result.data.options for value in item.costs),
        "unknown_cost_is_not_zero": all(not item.costs for item in result.data.options if item.route != "litigation"),
        "source_quote_located": result.data.source_checks[0].status == "original_text_checked",
        "semantic_review_still_pending": result.data.source_checks[0].semantic_support_checked is False,
        "goals_have_focus": all(item.dimensions for item in result.data.goal_focus if item.origin == "request"),
        "snapshot_and_selection_unchanged": repository.get_case(snapshot.case_id) == snapshot,
        "saved_and_idempotent": bool(result.output_refs) and service.compare_routes(request) == result,
    }
    assert all(checks.values()), checks
    report = dict(synthetic=True, checks=checks, request=request,
                  result=result.model_dump(mode="json"), limitation="合成联通演示，不证明真实案件法律适用正确。")
    (output / "result.json").write_text(json.dumps(report, ensure_ascii=False, indent=2,
        default=lambda value: value.model_dump(mode="json")), encoding="utf-8")
    print(json.dumps({"passed": len(checks), "report": str(output / "result.json")}, ensure_ascii=True))


if __name__ == "__main__":
    main()
