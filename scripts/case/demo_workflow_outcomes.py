"""Synthetic workflow feedback -> resumed tasks -> conditional analysis -> verification."""

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from backend.case import CaseService, SQLiteCaseRepository, load_development_case
from backend.case.models import CaseSnapshot
from backend.case.runtime import RepositoryCasePort


def synthetic_source(pointer):
    kind = "case" if pointer.source_id == "synthetic-case" else "statute"
    return dict(source_id=pointer.source_id, version=pointer.source_version, source_type=kind,
        title="合成接口演示资料，不是真实法律依据", locator={"paragraph": "synthetic-demo-only"},
        text="合成资料片段，仅用于接口联通验证。", url="https://example.com/synthetic-demo",
        jurisdiction="CN-Mainland", effective_from="2020-01-01",
        court="合成法院" if kind == "case" else None, decision_date="2025-01-01" if kind == "case" else None)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    output = parser.parse_args().output.resolve()
    if output.exists() and any(output.iterdir()):
        raise SystemExit("output must be new or empty")
    output.mkdir(parents=True, exist_ok=True)
    repository = SQLiteCaseRepository(output / "cases.db")
    case = repository.create_case(load_development_case())
    port = RepositoryCasePort(repository)
    port.set_stage(case.case_id, "litigation")  # Explicit synthetic user's choice, not a tool decision.
    service = CaseService(repository, source_resolver=synthetic_source)
    def context(identity):
        return dict(request_id=identity, case_id=case.case_id, experiment_run_id="synthetic-features07-08", deadline_at=None)
    def request(identity, **extra):
        current = repository.get_case(case.case_id)
        return dict(context=context(identity), case_version=current.case_version, case_snapshot=current, **extra)
    plan = service.get_route_workflow(request("plan-before", route="litigation"))
    assert plan.status == "insufficient", plan.error
    current = repository.get_case(case.case_id)
    quote = "我已完成本次管辖信息核对，实际受理及其他程序条件仍待核实。"
    reply = port.record_user_reply(case_id=case.case_id, expected_case_version=current.case_version,
        request_id="save-demo-feedback", message_id="workflow-feedback", actor_id="synthetic_user", text=quote,
        created_at=max(datetime.now(timezone.utc), current.updated_at))
    assert reply.status == "ok", reply.error
    current = repository.get_case(case.case_id)
    update = dict(context=context("record-progress"), case_version=current.case_version, route="litigation",
        expected_progress_version=0, plan_output_id=plan.output_refs[0], message_id="workflow-feedback",
        actor_id="synthetic_user", quote=quote, changes=[dict(step_id="jurisdiction", status="completed", note="用户反馈已完成本项核对")])
    progress = service.record_workflow_progress(update)
    assert progress.status == "ok", progress.error
    service = CaseService(SQLiteCaseRepository(repository.path), source_resolver=synthetic_source)
    resumed = service.get_route_workflow(request("plan-resumed", route="litigation"))
    analysis_request = request("outcome-analysis",
        source_selections=[dict(selection_id=kind, source_ref=dict(source_id="synthetic-" + kind, source_version="demo-v1"),
                                expected_kind="case" if kind == "case" else "statute", quote="合成资料片段，仅用于接口联通验证。") for kind in ("law", "case")],
        factors=[dict(factor_id="candidate-pro", claim_id="claim_repair", polarity="supporting", description="合成有利候选，语义尚待审查",
                      fact_ids=["f_claimant"], evidence_ids=["e_invoice"], source_selection_ids=["law"]),
                 dict(factor_id="candidate-adverse", claim_id="claim_repair", polarity="adverse", description="存在不同日期记载，需核对",
                      fact_ids=["f_date_user", "f_date_document"])],
        case_comparisons=[dict(comparison_id="comparison-demo", claim_id="claim_repair", source_selection_id="case",
                              fact_ids=["f_claimant"], similarities=["合成相似点"], differences=["合成差异，真实比较需成员二/四核验"])],
        scenarios=[dict(scenario_id="scenario-demo", claim_id="claim_repair", kind="support",
                        statement="待所列前提和法律适用核实后讨论支持情形；不是本案预测。", conditions=["材料及规则适用核实"],
                        factor_ids=["candidate-pro"], source_selection_ids=["law"])])
    analysis = service.analyze_outcomes(analysis_request)
    assert analysis.status == "insufficient", analysis.error
    saved = service.assets.get_output(case.case_id, analysis.output_refs[0])["output"]
    verification = service.verify_output(request("verify-analysis", candidate_output=saved, cited_sources=saved.source_refs))
    assert verification.status == "insufficient", verification.error
    unchanged = repository.get_case(case.case_id) == current
    changed = current.model_dump(mode="json")
    changed.update(case_version=current.case_version + 1, user_goals=["合成演示：实际目标已变化"],
                   updated_at=max(datetime.now(timezone.utc), current.updated_at).isoformat())
    repository.replace_case(CaseSnapshot.model_validate(changed), expected_case_version=current.case_version)
    reviewed = service.get_route_workflow(request("plan-review-needed", route="litigation"))
    checks = dict(
        core_and_conditional_steps_separate=len(resumed.data.steps) == 4 and len(resumed.data.conditional_steps) == 3,
        progress_saved=progress.data.revision == 1,
        retry_is_idempotent=service.record_workflow_progress(update) == progress,
        restart_retains_completion=resumed.data.steps[0].should_skip,
        reply_only_did_not_invalidate_plan=resumed.data.steps[0].status == "completed",
        domain_changes_trigger_review=reviewed.data.steps[0].status == "needs_review" and not reviewed.data.steps[0].should_skip,
        old_completion_preserved=reviewed.data.steps[0].progress.status == "completed",
        business_calls_do_not_mutate_snapshot=unchanged,
        both_factor_directions_kept={item.candidate.polarity for item in analysis.data.claims[0].factors} == {"supporting", "adverse"},
        pending_premises_visible=analysis.data.claims[0].factors[1].status == "premises_pending",
        sources_and_case_differences_present=bool(analysis.source_refs and analysis.data.claims[0].case_comparisons),
        candidate_not_certified=analysis.data.claims[0].scenarios[-1].status == "candidate_needs_review",
        semantic_verification_pending="semantic_citation_support" in verification.data.checks_not_performed,
        old_analysis_marked_stale=service.assets.get_output(case.case_id, analysis.output_refs[0])["stale"],
    )
    assert all(checks.values()), checks
    report = dict(synthetic=True, checks=checks, workflow_initial=plan, progress=progress, workflow_resumed=resumed,
                  workflow_after_change=reviewed, analysis=analysis, verification=verification,
                  limitation="全部分析候选和来源均为合成接口示例，不证明真实法律正确性或模型效果。")
    (output / "result.json").write_text(json.dumps(report, ensure_ascii=False, indent=2,
        default=lambda value: value.model_dump(mode="json")), encoding="utf-8")
    print(json.dumps({"passed": len(checks), "report": str(output / "result.json")}, ensure_ascii=True))


if __name__ == "__main__":
    main()
