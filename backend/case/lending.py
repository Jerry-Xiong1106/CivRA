"""Single-debt principal arithmetic using confirmed facts, with no interest rules."""
from decimal import localcontext

from .knowledge_bridge import normalize_dispute_type
from .models import ContractModel, Identifier, Money

PRINCIPAL_RULE_VERSION = "confirmed_principal_v1"


class PrincipalInputs(ContractModel):
    principal_fact_id: Identifier
    has_repaid_fact_id: Identifier
    repaid_principal_fact_id: Identifier | None = None


def principal_balance(snapshot, inputs):
    args = PrincipalInputs.model_validate(inputs)
    if normalize_dispute_type(snapshot.dispute_type) != "private_lending":
        raise ValueError("principal balance requires an explicitly identified lending case")
    active = [f for f in snapshot.facts if not f.superseded_by]
    facts = {f.fact_id: f for f in active}
    evidence = {e.evidence_id: e for e in snapshot.evidence}
    unresolved = []

    def read(identity, field, kind):
        f = facts.get(identity)
        if f is None or f.field != field or f.value_type != kind or f.value is None or f.confirmation_status != "confirmed":
            unresolved.append(field + ": needs a current confirmed fact")
            return None
        if f.evidence_ids and not any(evidence[e].status != "excluded" for e in f.evidence_ids):
            unresolved.append(field + ": all supporting evidence excluded")
        value = Money.model_validate(f.value) if kind == "money" else f.value
        for other in active:
            if other.field != field or other.value is None:
                continue
            if other.evidence_ids and not any(evidence[e].status != "excluded" for e in other.evidence_ids):
                continue
            candidate = Money.model_validate(other.value) if other.value_type == "money" else other.value
            equal = (candidate.decimal, candidate.currency) == (value.decimal, value.currency) if isinstance(candidate, Money) and isinstance(value, Money) else candidate == value
            if not equal:
                unresolved.append(field + ": conflicting active records")
                break
        return value

    principal = read(args.principal_fact_id, "loan_principal", "money")
    has_repaid = read(args.has_repaid_fact_id, "has_repaid", "boolean")
    repaid = None
    if has_repaid is True:
        if args.repaid_principal_fact_id is None:
            unresolved.append("repaid_principal: repayment allocation to this debt's principal is not confirmed")
        else:
            repaid = read(args.repaid_principal_fact_id, "repaid_principal", "money")
    elif has_repaid is False:
        repaid = Money(amount="0", currency=principal.currency) if principal else None
        if args.repaid_principal_fact_id is not None:
            supplied = read(args.repaid_principal_fact_id, "repaid_principal", "money")
            if supplied is not None and (supplied.decimal != 0 or principal and supplied.currency != principal.currency):
                unresolved.append("has_repaid: conflicts with recorded principal repayment")
        for f in active:
            if f.field == "repaid_principal" and f.value_type == "money" and f.value is not None and Money.model_validate(f.value).decimal > 0:
                if not f.evidence_ids or any(evidence[e].status != "excluded" for e in f.evidence_ids):
                    unresolved.append("has_repaid: positive repayment exists in active records")
                    break
    amounts = {}
    if principal is not None and repaid is not None:
        if principal.currency != repaid.currency:
            unresolved.append("currency: principal and repayment currencies differ")
        if repaid.decimal > principal.decimal:
            unresolved.append("repaid_principal: exceeds principal; verify debt/allocation")
        if not unresolved:
            with localcontext() as context:
                context.prec = max(64, len(principal.amount) + len(repaid.amount) + 4)
                balance = principal.decimal - repaid.decimal
            amounts = {"principal": principal, "repaid_principal": repaid,
                       "remaining_principal": Money(amount=format(balance, "f"), currency=principal.currency)}
    return amounts, "remaining_principal = confirmed loan_principal - confirmed repaid_principal", [
        "仅核算本案单笔借款本金；还款归属及用于归还本金的金额须明确确认。",
        "未知还款情况不按零处理；未计算利息、逾期费用或抵充顺序。"], list(dict.fromkeys(unresolved))
