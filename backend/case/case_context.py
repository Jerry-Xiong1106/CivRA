"""Shared fact eligibility and evidence-grounded context for case operations."""

from .models import ContractModel, Finding


UNKNOWN_TEXT = {"未知", "不知道", "不清楚", "不详", "待补充", "待核对", "待核实", "待确认", "未提供", "不确定", "unknown"}


class RouteCaseContext(ContractModel):
    confirmed_fact_ids: list[str]
    pending_fact_ids: list[str]
    evidence_ids: list[str]
    excluded_evidence_ids: list[str]
    claim_ids: list[str]
    findings: list[Finding]
    evidence_rule_profile: str
    limitations: list[str]


def active_facts(snapshot):
    """Exclude superseded facts and facts supported only by excluded evidence."""
    usable_evidence = {item.evidence_id for item in snapshot.evidence if item.status != "excluded"}
    return [fact for fact in snapshot.facts if fact.superseded_by is None and
            (not fact.evidence_ids or any(key in usable_evidence for key in fact.evidence_ids))]


def has_known_value(fact):
    """Check for an actual value; confirmation and conflicts are separate checks."""
    return fact.value is not None and not (fact.value_type == "text" and fact.value.strip().lower() in UNKNOWN_TEXT)


def build_case_context(snapshot, report):
    """Combine eligible facts with an existing evidence report, without rerunning it."""
    findings = report.missing_facts + report.conflicts + report.duplicates + report.material_gaps
    conflicting = {key for item in report.conflicts for key in item.fact_ids}
    facts = active_facts(snapshot)
    confirmed = [fact.fact_id for fact in facts if fact.confirmation_status == "confirmed"
                 and has_known_value(fact) and fact.fact_id not in conflicting]
    return RouteCaseContext(
        confirmed_fact_ids=confirmed, pending_fact_ids=[fact.fact_id for fact in facts if fact.fact_id not in confirmed],
        evidence_ids=[item.evidence_id for item in snapshot.evidence if item.status != "excluded"],
        excluded_evidence_ids=[item.evidence_id for item in snapshot.evidence if item.status == "excluded"],
        claim_ids=[item.claim_id for item in snapshot.claims if item.status != "withdrawn"],
        findings=findings, evidence_rule_profile=report.rule_profile, limitations=report.limitations)
