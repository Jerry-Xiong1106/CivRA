"""Read-only intake summaries and explicit fact-review preparation.

No LLM, HTTP route or Agent tool is registered here. A trusted application records
the user's message first. This module prepares an existing UpdateCaseRequest;
CaseService.update_case remains the single persistence path for the changes.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Literal

from pydantic import Field, JsonValue, ValidationError, model_validator

from .contracts import CaseChanges, UpdateCaseRequest
from ..contracts import CaseRequest
from ..materials.contracts import CheckEvidenceRequest
from ..materials.evidence import FIELD_LABELS, check_evidence
from ..case_context import UNKNOWN_TEXT
from ..models import (
    BusinessError, BusinessResult, CaseSnapshot, Claim, Confirmation, ContractModel,
    Fact, Identifier, UserStatement,
)
from ..errors import CaseNotFound, VersionConflict


INTAKE_VERSION = "case-intake-0.1.0"
UNKNOWN_VALUES = UNKNOWN_TEXT | {"暂不清楚", "暂时不清楚", "暂时不知道", "待确认", "未确认"}
LABELS = {**FIELD_LABELS, "defendant_name": "对方姓名或名称", "claimant_identity": "当事人身份",
          "claimant_contact": "当事人联系方式", "accident_location": "事故地点",
          "medical_amount": "医疗金额", "loan_principal": "借款本金", "loan_delivery": "款项交付情况",
          "repayment_history": "还款记录", "contract_details": "合同情况"}
STATE_LABELS = {"confirmed": "已确认", "unconfirmed": "待确认", "disputed": "有异议",
                "unknown": "未知", "superseded": "已被修正", "excluded_source": "来源已排除"}


class SummaryFact(ContractModel):
    fact: Fact
    label: str
    display_value: str
    state: Literal["confirmed", "unconfirmed", "disputed", "unknown", "superseded", "excluded_source"]
    group: Literal["parties", "events", "amounts", "other"]
    has_conflict: bool


class PendingIntakeItem(ContractModel):
    field: str
    fact_ids: list[str] = Field(default_factory=list)
    reason: Literal["missing", "conflict", "unknown", "unconfirmed", "disputed"]
    question: str


class CaseIntakeSummary(ContractModel):
    intake_version: str = INTAKE_VERSION
    dispute_type: str | None
    jurisdiction: str | None
    stage: str
    user_goals: list[str]
    statements: list[UserStatement]
    facts: list[SummaryFact]
    claims: list[Claim]
    pending_items: list[PendingIntakeItem]
    text: str
    limitations: list[str]


class FactReviewAction(ContractModel):
    fact_id: Identifier
    action: Literal["confirm", "defer", "dispute", "correct"]
    new_fact_id: Identifier | None = None
    replacement_value: JsonValue = None
    supersede_fact_ids: list[Identifier] = Field(default_factory=list)

    @model_validator(mode="after")
    def valid_operation(self):
        if self.action == "correct":
            if self.new_fact_id is None or self.replacement_value is None:
                raise ValueError("correct needs a new fact ID and a known replacement value")
        elif self.new_fact_id is not None or self.replacement_value is not None:
            raise ValueError("only correct may supply replacement fields")
        if len(set(self.supersede_fact_ids)) != len(self.supersede_fact_ids):
            raise ValueError("superseded fact IDs must be unique")
        if self.action in ("defer", "dispute") and self.supersede_fact_ids:
            raise ValueError("defer/dispute cannot resolve other facts")
        return self


class FactReviewRequest(CaseRequest):
    message_id: Identifier
    reviewer_id: Identifier
    actions: list[FactReviewAction] = Field(min_length=1)

    @model_validator(mode="after")
    def unique_targets(self):
        if len({item.fact_id for item in self.actions}) != len(self.actions):
            raise ValueError("review each fact at most once per request")
        return self


class PreparedFactReview(ContractModel):
    update_request: UpdateCaseRequest | None
    changed_fact_ids: list[str]
    deferred_fact_ids: list[str]
    message_id: str
    limitations: list[str]


def _unknown(fact):
    return fact.value is None or (fact.value_type == "text" and fact.value.strip().lower() in UNKNOWN_VALUES)


def _display(fact):
    if fact.value is None:
        return "未知"
    if fact.value_type == "money":
        return f"{fact.value['amount']} {fact.value['currency']}"
    if fact.value_type == "boolean":
        return "是" if fact.value else "否"
    return str(fact.value)


def _group(fact):
    if fact.value_type == "money":
        return "amounts"
    if fact.value_type == "date":
        return "events"
    if fact.field.startswith(("claimant_", "defendant_", "respondent_", "parties.")):
        return "parties"
    return "other"


def _error(case_id, version, code, message):
    return BusinessResult(case_id=case_id, case_version=version, status="error", data=None,
                          summary=message, error=BusinessError(code=code, message=message))


class CaseIntakeService:
    """Standalone Member 3 business API; reads a shared repository without writes."""

    def __init__(self, repository):
        self.repository = repository

    def _read(self, request):
        if request.context.deadline_at and datetime.now(timezone.utc) >= request.context.deadline_at:
            raise TimeoutError("请求已过期。")
        snapshot = self.repository.get_case(request.context.case_id)
        if snapshot.case_version != request.case_version:
            raise VersionConflict(request.case_version, snapshot.case_version)
        return snapshot

    def summarize(self, request: CaseRequest | dict) -> BusinessResult[CaseIntakeSummary]:
        case_id, version = None, None
        try:
            request = CaseRequest.model_validate(request.model_dump(mode="json") if isinstance(request, CaseRequest) else request)
            case_id, version = request.context.case_id, request.case_version
            snapshot = self._read(request)
            inspection = check_evidence(CheckEvidenceRequest(**request.model_dump(), case_snapshot=snapshot))
            if inspection.status == "error":
                return inspection
            report = inspection.data
            conflicts = {key for finding in report.conflicts for key in finding.fact_ids}
            evidence = {item.evidence_id: item for item in snapshot.evidence}
            pending = [PendingIntakeItem(field=item.affected_fields[0], fact_ids=item.fact_ids,
                reason="conflict" if item.category == "conflict" else "missing", question=item.message)
                for item in report.conflicts + report.missing_facts]
            if not snapshot.dispute_type:
                pending.append(PendingIntakeItem(field="case.dispute_type", reason="missing", question="纠纷类型尚未确定，请补充情况。"))
            if not snapshot.facts:
                pending.append(PendingIntakeItem(field="case.facts", reason="missing", question="尚无结构化事实，请整理或补充已有陈述。"))
            items = []
            for fact in snapshot.facts:
                if fact.superseded_by:
                    state = "superseded"
                elif fact.evidence_ids and all(evidence[key].status == "excluded" for key in fact.evidence_ids):
                    state = "excluded_source"
                elif _unknown(fact):
                    state = "unknown"
                else:
                    state = fact.confirmation_status
                label = LABELS.get(fact.field, LABELS.get(fact.field.rsplit(".", 1)[-1], fact.field))
                items.append(SummaryFact(fact=fact.model_copy(deep=True), label=label, display_value=_display(fact),
                                        state=state, group=_group(fact), has_conflict=fact.fact_id in conflicts))
                if state in ("unknown", "unconfirmed", "disputed") and fact.fact_id not in conflicts:
                    pending.append(PendingIntakeItem(field=fact.field, fact_ids=[fact.fact_id], reason=state,
                        question=f"请核对{label}（{STATE_LABELS[state]}）；可以确认、暂缓、提出异议或修正。"))
            group_order = {"parties": 0, "events": 1, "amounts": 2, "other": 3}
            items.sort(key=lambda item: (item.state in ("superseded", "excluded_source"), group_order[item.group],
                       item.fact.value if item.fact.value_type == "date" and item.fact.value else "", item.fact.field, item.fact.fact_id))
            text = ["案情核对摘要", f"纠纷类型：{snapshot.dispute_type or '待确定'}", f"当前阶段：{snapshot.stage}",
                    "用户目标：" + ("；".join(snapshot.user_goals) or "未填写")]
            if not items:
                text.append("尚无结构化事实；原始陈述保留在 statements 中，未自动转换为已确认事实。")
            for item in items:
                warning = "；存在冲突" if item.has_conflict else ""
                text.append(f"- {item.label}：{item.display_value}［{STATE_LABELS[item.state]}{warning}］（{item.fact.fact_id}）")
            text.append("诉求")
            text.extend(f"- {claim.description}［{claim.status}］" +
                        (f" {claim.amount.amount} {claim.amount.currency}" if claim.amount else "") for claim in snapshot.claims)
            if not snapshot.claims:
                text.append("尚未记录结构化诉求。")
            data = CaseIntakeSummary(dispute_type=snapshot.dispute_type, jurisdiction=snapshot.jurisdiction,
                stage=snapshot.stage, user_goals=list(snapshot.user_goals), statements=[s.model_copy(deep=True) for s in snapshot.statements],
                facts=items, claims=[c.model_copy(deep=True) for c in snapshot.claims], pending_items=pending, text="\n".join(text),
                limitations=["这是结构化记录的确定性整理，不做纠纷分类或自然语言事实提取。",
                             "已确认仅表示记录了用户确认，不代表法院认定；冲突、旧记录和已排除来源仍显式保留。",
                             "缺口沿用现有工程规则，不是完整法律要件或法定材料清单；金额不自动合计。",
                             "text为纯文本；前端需按文本展示，不能作为可信HTML插入。"])
            return BusinessResult(case_id=case_id, case_version=version, status="insufficient" if pending else "ok",
                summary=f"已整理 {len(items)} 项事实和 {len(pending)} 项待核对事项；未修改案件。", data=data)
        except VersionConflict as exc:
            return _error(case_id, exc.actual, "version_conflict", str(exc))
        except CaseNotFound:
            return _error(case_id, version, "not_found", "案件不存在。")
        except TimeoutError as exc:
            return _error(case_id, version, "timeout", str(exc))
        except (ValidationError, ValueError) as exc:
            return _error(case_id, version, "invalid_request", str(exc).splitlines()[0])

    def prepare_review(self, request: FactReviewRequest | dict) -> BusinessResult[PreparedFactReview]:
        case_id, version = None, None
        try:
            request = FactReviewRequest.model_validate(request.model_dump(mode="json") if isinstance(request, FactReviewRequest) else request)
            case_id, version = request.context.case_id, request.case_version
            snapshot = self._read(request)
            statement = next((item for item in snapshot.statements if item.message_id == request.message_id), None)
            if statement is None or statement.actor_id != request.reviewer_id:
                raise ValueError("review must reference this case's saved message and its authenticated author")
            facts = {fact.fact_id: fact.model_copy(deep=True) for fact in snapshot.facts}
            evidence = {item.evidence_id: item for item in snapshot.evidence}
            changes, deferred, reserved = {}, [], set()
            confirmation = Confirmation(message_id=statement.message_id, confirmed_by=statement.actor_id,
                                        confirmed_at=statement.created_at)

            def active(key):
                if key not in facts or facts[key].superseded_by:
                    raise ValueError("review target must be an existing active fact")
                fact = facts[key]
                if fact.confirmation and fact.confirmation.confirmed_at > statement.created_at:
                    raise ValueError("an older message cannot replace a newer confirmation")
                return fact

            def reserve(key):
                if key in reserved:
                    raise ValueError("review operations overlap on a fact")
                reserved.add(key)

            for action in request.actions:
                fact = active(action.fact_id)
                reserve(fact.fact_id)
                if action.action == "defer":
                    deferred.append(fact.fact_id)
                    continue
                if action.action == "dispute":
                    changed = fact.model_copy(update={"confirmation_status": "disputed", "confirmation": None})
                    if changed != fact:
                        changes[fact.fact_id] = changed
                    continue
                if action.action == "confirm":
                    if _unknown(fact):
                        raise ValueError("unknown values cannot be confirmed; defer or supply a correction")
                    if fact.evidence_ids and all(evidence[key].status == "excluded" for key in fact.evidence_ids):
                        raise ValueError("cannot confirm a fact backed only by excluded evidence")
                    target = fact.model_copy(update={"confirmation_status": "confirmed", "confirmation": confirmation})
                else:
                    if action.new_fact_id in facts or action.new_fact_id in changes:
                        raise ValueError("correction must use a new fact ID")
                    reserve(action.new_fact_id)
                    target = Fact(fact_id=action.new_fact_id, field=fact.field, value_type=fact.value_type,
                        value=action.replacement_value, provenance="user_statement", confirmation_status="confirmed",
                        statement_refs=[statement.message_id], confirmation=confirmation)
                    if _unknown(target):
                        raise ValueError("a correction must supply a known value")
                    changes[fact.fact_id] = fact.model_copy(update={"superseded_by": target.fact_id})
                if target != fact:
                    changes[target.fact_id] = target
                for key in action.supersede_fact_ids:
                    other = active(key)
                    reserve(key)
                    if (other.field, other.value_type) != (target.field, target.value_type):
                        raise ValueError("explicit conflict resolution must stay within the same field and value type")
                    changes[key] = other.model_copy(update={"superseded_by": target.fact_id})
            update = None
            if changes:
                preview = snapshot.model_dump(mode="json")
                preview["facts"] = [item.model_dump(mode="json") for item in {**facts, **changes}.values()]
                # Validate the entire resulting graph, including conflicting confirmed values.
                CaseSnapshot.model_validate(preview)
                update = UpdateCaseRequest(context=request.context, expected_case_version=version,
                    changes=CaseChanges(upsert_facts=list(changes.values())), change_reason="explicit_structured_fact_review")
            data = PreparedFactReview(update_request=update, changed_fact_ids=list(changes), deferred_fact_ids=deferred,
                message_id=statement.message_id, limitations=["只准备变更，不写数据库。由调用方将update_request交给现有CaseService.update_case。",
                    "不解释消息原文的语义。actions必须来自用户明确的结构化操作，或另行验证的语义转换。",
                    "暂缓保持原状态；冲突仅按显式supersede_fact_ids处理，不自动选取一个候选值。",
                    "应用先做鉴权并保存原始回复；保存后重试应复用同一update_request，不重新解释消息。"])
            return BusinessResult(case_id=case_id, case_version=version, status="ok", data=data,
                summary=f"已准备 {len(changes)} 项事实变更，暂缓 {len(deferred)} 项；尚未保存。")
        except VersionConflict as exc:
            return _error(case_id, exc.actual, "version_conflict", str(exc))
        except CaseNotFound:
            return _error(case_id, version, "not_found", "案件不存在。")
        except TimeoutError as exc:
            return _error(case_id, version, "timeout", str(exc))
        except (ValidationError, ValueError) as exc:
            return _error(case_id, version, "invalid_request", str(exc).splitlines()[0])
