"""Synthetic query preparation, local source review, material total and separate fee estimate."""

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from backend.case import CaseService, SQLiteCaseRepository, load_development_case
from backend.tools.case_results import to_runtime_tool_result
from backend.case.calculation.cost_summary import MATERIAL_SUMMARY_VERSION
from backend.case.calculation.fee_rules import PROPERTY_FEE_RULE_VERSION
from backend.case.models import BusinessOutput
from backend.agent.case_adapter import RepositoryCasePort
from demo_support import DEMO_REPLY, confirmed_demo_changes


def run_demo(directory):
    directory = Path(directory)
    if directory.exists() and any(directory.iterdir()):
        raise ValueError("choose a new or empty output directory")
    directory.mkdir(parents=True, exist_ok=True)
    repo = SQLiteCaseRepository(directory/"demo.sqlite3")
    case = repo.create_case(load_development_case())
    synthetic_source = {"source_id": "synthetic.case.research-demo", "version": "synthetic-v1", "source_type": "case",
        "title": "合成案例，仅用于接口演示，非真实裁判文书", "locator": {"paragraph": "合成段落1"}, "url": None,
        "text": "【合成案例】本段仅用于展示案件来源、版本和引文定位，不含真实裁判结论。",
        "court": "合成法院名称（非真实法院）", "decision_date": "2026-01-01"}
    def resolver(pointer):
        if (pointer.source_id, pointer.source_version) != (synthetic_source["source_id"], synthetic_source["version"]):
            raise KeyError("only a synthetic test case source is available")
        return synthetic_source
    service = CaseService(repo, source_resolver=resolver)
    def context(key):
        return {"request_id": key, "case_id": case.case_id, "experiment_run_id": "synthetic-features04-05", "deadline_at": None}
    reply = RepositoryCasePort(repo).record_user_reply(case_id=case.case_id, expected_case_version=1,
        request_id="synthetic-reply", message_id="msg_demo_reply", actor_id="synthetic_user", text=DEMO_REPLY,
        created_at=datetime.now(timezone.utc))
    if reply.status != "ok":
        raise RuntimeError(reply.model_dump_json())
    snapshot = repo.get_case(case.case_id)
    saved = service.update_case({"context": context("synthetic-confirm"), "expected_case_version": snapshot.case_version,
        "changes": confirmed_demo_changes(snapshot), "change_reason": "synthetic user's explicit review"})
    if saved.status != "ok":
        raise RuntimeError(saved.model_dump_json())
    current = saved.data.case_snapshot
    plan = service.prepare_research({"context": context("prepare"), "case_version": current.case_version,
        "knowledge_version": "demo-placeholder-not-an-actual-index", "as_of_date": "2026-11-09"})
    selected = {"context": context("summary"), "case_version": current.case_version,
        "calculation_type": "materials_breakdown", "rule_version": MATERIAL_SUMMARY_VERSION,
        "inputs": {"fact_ids": ["f_repair_amount", "f_repair_amount_copy"]}}
    detail = service.summarize_material_costs(selected)
    material_calculation = service.calculate_costs(selected)
    fee = service.calculate_costs({"context": context("fee"), "case_version": current.case_version,
        "calculation_type": "property_case_acceptance_fee", "rule_version": PROPERTY_FEE_RULE_VERSION,
        # Explicit synthetic user choice; never inferred automatically from the material total.
        "inputs": {"claim_value": {"amount": "3000.00", "currency": "CNY"}, "case_category": "ordinary_property",
                   "procedure": "first_instance_ordinary", "adjustment": "none", "filing_date": "2026-11-09"}})
    sources = service.review_research_sources({"context": context("sources"), "case_version": current.case_version,
        "reference_date": "2026-11-09", "selections": [{"source_ref": fee.source_refs[0], "expected_kind": "statute"},
            {"source_ref": {"source_id": synthetic_source["source_id"], "source_version": synthetic_source["version"]}, "expected_kind": "case"}]})
    candidate = BusinessOutput(output_id="synthetic-cost-review", output_version=1, case_id=case.case_id,
        case_version=current.case_version, output_type="analysis", text="合成演示：材料金额与受理费分别列出。",
        source_refs=fee.source_refs, structured_data={"calculations": [material_calculation.data.model_dump(mode="json"), fee.data.model_dump(mode="json")]})
    verified = service.verifier.verify_output({"context": context("replay"), "case_version": current.case_version,
        "case_snapshot": current, "candidate_output": candidate, "cited_sources": fee.source_refs})
    checks = {
        "query_plan_prepared_without_retrieval": len(plan.data.queries) == 3,
        "confirmed_event_date_preserved": all(query.filters["incident_date"] == "2026-05-12" for query in plan.data.queries),
        "materials_total_is_3000": detail.data.total.amount == "3000.00",
        "excluded_copy_remains_visible": detail.data.rows[1].state == "excluded",
        "fee_is_separate_50": fee.data.amounts["fee_estimate"].amount == "50.00" and "total" not in fee.data.amounts,
        "statute_and_synthetic_case_separated": len(sources.data.statutes) == len(sources.data.cases) == 1,
        "fee_source_available_to_runtime_adapter": bool(to_runtime_tool_result(fee, source_resolver=service.get_source)["source_refs"]),
        "calculations_replay_without_calculation_finding": not any(item.category == "calculation" for item in verified.data.findings),
        "semantic_review_stays_unperformed": "semantic_fact_faithfulness" in verified.data.checks_not_performed,
        "case_snapshot_unchanged": repo.get_case(case.case_id) == current,
    }
    report = {"scope": "Synthetic development case and synthetic comparison-case source. Query plan is not real retrieval; fee source is a pinned official-text excerpt.",
        "checks_passed": all(checks.values()), "checks": checks,
        **{name: result.model_dump(mode="json") for name, result in [("plan", plan), ("material_summary", detail),
            ("material_calculation", material_calculation), ("fee", fee), ("sources", sources), ("verification", verified)]}}
    (directory/"result.json").write_text(json.dumps(report, ensure_ascii=False, indent=2)+"\n", encoding="utf-8")
    return report


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, required=True)
    report = run_demo(parser.parse_args().output_dir)
    print(json.dumps({"checks_passed": report["checks_passed"], "checks": report["checks"]}, ensure_ascii=False, indent=2))
    raise SystemExit(not report["checks_passed"])
