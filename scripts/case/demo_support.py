"""Synthetic, explicit user answers used only by demos/tests, never by business tools."""

from datetime import datetime, timezone

from backend.case.intake.contracts import CaseChanges
from backend.case.models import CaseSnapshot, Confirmation, Fact

DEMO_REPLY = (
    "我确认事故日期为2026-05-12，材料里的2026-05-13记载有误，请保留原文并以我确认的日期处理。"
    "责任认定信息是示例乙全责；保险情况是已投保，具体保险材料待补充。"
    "两张维修票据是同一张，保留e_invoice，排除e_invoice_copy；维修票据金额为3000.00元。"
)


def confirmed_demo_changes(snapshot: CaseSnapshot, message_id="msg_demo_reply") -> CaseChanges:
    """Interpret only the fixed DEMO_REPLY; not a general natural-language extractor."""
    statement = next(item for item in snapshot.statements if item.message_id == message_id)
    confirmation = Confirmation(message_id=message_id, confirmed_by=statement.actor_id,
                                confirmed_at=max(datetime.now(timezone.utc), statement.created_at))
    chosen = next(fact for fact in snapshot.facts if fact.field == "accident_date" and fact.value == "2026-05-12")
    facts = []
    for fact in snapshot.facts:
        if fact.fact_id == chosen.fact_id or fact.fact_id == "f_repair_amount":
            facts.append(fact.model_copy(update={"confirmation_status": "confirmed", "confirmation": confirmation}))
        elif fact.field == "accident_date":
            facts.append(fact.model_copy(update={"superseded_by": chosen.fact_id}))
    for fact_id, field, value in [
        ("f_insurance_reply", "insurance", "已投保，具体保险材料待补充"),
        ("f_liability_reply", "liability_finding", "示例乙承担全部责任（用户陈述，仍需核对材料）"),
    ]:
        facts.append(Fact(fact_id=fact_id, field=field, value_type="text", value=value,
            provenance="user_statement", confirmation_status="confirmed", statement_refs=[message_id], confirmation=confirmation))
    duplicate = next(item for item in snapshot.evidence if item.evidence_id == "e_invoice_copy")
    return CaseChanges(upsert_facts=facts, upsert_evidence=[duplicate.model_copy(update={
        "status": "excluded", "duplicate_of": "e_invoice", "exclusion": confirmation})])
