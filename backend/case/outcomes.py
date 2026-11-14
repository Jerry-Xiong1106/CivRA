"""Claim-level conditional outcome organization; no LLM calls or probability scores."""

from typing import Literal
import re

from pydantic import Field, ValidationError, model_validator

from .business_support import deadline, failure, review_sources, unique
from .contracts import SnapshotRequest
from .evidence import check_evidence, _active_facts
from .models import (
    BusinessResult, Claim, ContractModel, Evidence, Fact, Finding, Identifier, OutputLink,
)
from .research import SourceSelection
from .route_comparison import _case_context
from .route_models import RouteSourceExcerpt


OUTCOME_VERSION = "case-outcomes-0.1.0"
OutcomeKind = Literal["support", "partial_support", "not_supported", "undetermined"]


class OutcomeSourceSelection(SourceSelection):
    selection_id: Identifier
    quote: str = Field(min_length=1, max_length=2000)

    @model_validator(mode="after")
    def nonblank_quote(self):
        if not self.quote.strip():
            raise ValueError("outcome source quote must not be blank")
        return self


class OutcomeFactorInput(ContractModel):
    factor_id: Identifier
    claim_id: Identifier
    polarity: Literal["supporting", "adverse", "uncertain"]
    description: str = Field(min_length=1, max_length=2000)
    fact_ids: list[Identifier] = Field(default_factory=list, max_length=30)
    evidence_ids: list[Identifier] = Field(default_factory=list, max_length=30)
    source_selection_ids: list[Identifier] = Field(default_factory=list, max_length=30)

    @model_validator(mode="after")
    def identified_basis(self):
        if not self.description.strip() or not (self.fact_ids or self.evidence_ids or self.source_selection_ids):
            raise ValueError("a candidate factor requires text and at least one explicit reference")
        return self


class OutcomeCaseComparison(ContractModel):
    comparison_id: Identifier
    claim_id: Identifier
    source_selection_id: Identifier
    fact_ids: list[Identifier] = Field(min_length=1, max_length=30)
    similarities: list[str] = Field(default_factory=list, max_length=20)
    differences: list[str] = Field(default_factory=list, max_length=20)


class OutcomeScenarioInput(ContractModel):
    scenario_id: Identifier
    claim_id: Identifier
    kind: OutcomeKind
    statement: str = Field(min_length=1, max_length=2000)
    conditions: list[str] = Field(min_length=1, max_length=20)
    factor_ids: list[Identifier] = Field(min_length=1, max_length=30)
    source_selection_ids: list[Identifier] = Field(default_factory=list, max_length=30)

    @model_validator(mode="after")
    def conditional_not_probability(self):
        text = " ".join([self.statement] + self.conditions)
        if not self.statement.strip() or any(not item.strip() for item in self.conditions):
            raise ValueError("scenario statement and every condition must be nonblank")
        if self.scenario_id.startswith("template:"):
            raise ValueError("template: scenario IDs are reserved for unapplied framework branches")
        if re.search(r"(?:胜诉|获支持|成功)(?:概率|率).{0,16}(?:[0-9%％]|百分之)|(?:win|success)[ _-]*probability\s*[:=]?\s*[0-9]", text, re.I):
            raise ValueError("uncalibrated probability claims are not supported")
        return self


class OutcomeAnalysisRequest(SnapshotRequest):
    claim_ids: list[Identifier] | None = Field(default=None, min_length=1, max_length=30)
    source_selections: list[OutcomeSourceSelection] = Field(default_factory=list, max_length=30)
    factors: list[OutcomeFactorInput] = Field(default_factory=list, max_length=60)
    case_comparisons: list[OutcomeCaseComparison] = Field(default_factory=list, max_length=30)
    scenarios: list[OutcomeScenarioInput] = Field(default_factory=list, max_length=30)

    @model_validator(mode="after")
    def unique_identifiers(self):
        if self.claim_ids is not None and len(set(self.claim_ids)) != len(self.claim_ids):
            raise ValueError("claim_ids must be unique")
        for items, field in ((self.source_selections, "selection_id"), (self.factors, "factor_id"),
                             (self.case_comparisons, "comparison_id"), (self.scenarios, "scenario_id")):
            if len({getattr(item, field) for item in items}) != len(items):
                raise ValueError(field + " must be unique")
        return self


class OutcomeSourceCheck(ContractModel):
    selection_id: str
    status: Literal["original_text_checked", "rejected"]
    source: RouteSourceExcerpt | None
    reason: str


class OutcomeFactorReview(ContractModel):
    candidate: OutcomeFactorInput
    status: Literal["references_checked", "premises_pending"]
    confirmed_fact_ids: list[str]
    pending_fact_ids: list[str]
    semantic_support_checked: Literal[False] = False
    limitations: list[str]


class OutcomeCaseReview(ContractModel):
    candidate: OutcomeCaseComparison
    semantic_comparison_checked: Literal[False] = False
    limitations: list[str]


class ConditionalOutcome(ContractModel):
    scenario_id: str
    kind: OutcomeKind
    origin: Literal["template", "caller"]
    status: Literal["template_not_applied", "candidate_needs_review", "premises_pending"]
    statement: str
    conditions: list[str]
    factor_ids: list[str]
    source_selection_ids: list[str]
    semantic_support_checked: Literal[False] = False


class ClaimOutcome(ContractModel):
    claim: Claim
    confirmed_facts: list[Fact]
    pending_facts: list[Fact]
    available_evidence: list[Evidence]
    excluded_evidence_ids: list[str]
    finding_ids: list[str]
    factors: list[OutcomeFactorReview]
    case_comparisons: list[OutcomeCaseReview]
    scenarios: list[ConditionalOutcome]
    unresolved_items: list[str]


class OutcomeAnalysisData(ContractModel):
    implementation_version: str = OUTCOME_VERSION
    claims: list[ClaimOutcome]
    findings: list[Finding]
    sources: list[OutcomeSourceCheck]
    rejected_candidates: list[dict]
    output_links: list[OutputLink]
    checks_performed: list[str]
    checks_not_performed: list[str]
    limitations: list[str]


def _templates(claim_id):
    definitions = [
        ("support", "若相关责任、请求范围和全部必要事实获得充分支持，可进一步讨论本项请求获支持的情形。",
         ["必要事实与证据对应关系得到核实", "存在适用法律依据并核对完整条件", "不利因素及抗辩已被审查"]),
        ("partial_support", "若仅部分请求范围或金额具备事实及法律依据，可进一步讨论部分支持的情形。",
         ["明确可支持与仍有争议的具体项目", "核对部分支持的事实、金额及法律依据；不按材料数量推算比例"]),
        ("undetermined", "当前仅完成结构化整理，尚不能据此判断法院将支持多少或是否支持。",
         ["补齐本项列出的缺口并核对冲突", "审查证据真实性、证明力、法律适用及不利材料"]),
    ]
    return [ConditionalOutcome(scenario_id=f"template:{claim_id}:{kind}", kind=kind, origin="template",
        status="template_not_applied", statement=statement, conditions=conditions, factor_ids=[], source_selection_ids=[])
        for kind, statement, conditions in definitions]


class OutcomeAnalyzer:
    def __init__(self, *, evidence_checker=None, source_reviewer=None):
        self.evidence_checker, self.source_reviewer = evidence_checker, source_reviewer

    def build(self, payload):
        case_id, version = None, None
        try:
            request = OutcomeAnalysisRequest.model_validate(payload.model_dump(mode="json") if hasattr(payload, "model_dump") else payload)
            snapshot = request.case_snapshot
            case_id, version = snapshot.case_id, snapshot.case_version
            deadline(request)
            active_claims = {item.claim_id: item for item in snapshot.claims if item.status != "withdrawn"}
            chosen = request.claim_ids if request.claim_ids is not None else list(active_claims)
            if len(chosen) > 30 or set(chosen) - active_claims.keys():
                raise ValueError("select at most 30 known, nonwithdrawn claims")
            chosen_set = set(chosen)
            if any(item.claim_id not in chosen_set for item in request.factors + request.case_comparisons + request.scenarios):
                raise ValueError("candidate belongs to an unselected claim")
            report = (self.evidence_checker or check_evidence)({"context": request.context, "case_version": version, "case_snapshot": snapshot})
            if report.status == "error":
                return BusinessResult(case_id=case_id, case_version=version, status="error", data=None,
                    summary="证据检查未完成，未生成结果分析。", error=report.error)
            context = _case_context(snapshot, report.data)
            confirmed = set(context.confirmed_fact_ids)
            active_facts = {item.fact_id: item for item in _active_facts(snapshot)}
            evidence = {item.evidence_id: item for item in snapshot.evidence if item.status != "excluded"}
            selections = [SourceSelection(source_ref=item.source_ref, expected_kind=item.expected_kind, quote=item.quote)
                          for item in request.source_selections]
            cards, source_issues = review_sources(request, selections, self.source_reviewer)
            source_checks, accepted_sources = [], {}
            for selection in request.source_selections:
                card = cards.get((selection.source_ref.source_id, selection.source_ref.source_version,
                                  selection.expected_kind, selection.quote))
                if card:
                    accepted_sources[selection.selection_id] = card
                source_checks.append(OutcomeSourceCheck(selection_id=selection.selection_id,
                    status="original_text_checked" if card else "rejected", source=card.model_dump() if card else None,
                    reason="仅核对来源身份、版本及原文定位，未判断结论支持或案件适用性。" if card else
                           "原文未通过核对；请提供明确版本和连续片段。"))
            rejected, factors, comparisons, scenarios = [], {}, {}, {}
            def reject(kind, identity, reason):
                rejected.append({"kind": kind, "id": identity, "reason": reason})
            for factor in request.factors:
                deadline(request)
                if set(factor.fact_ids) - active_facts.keys() or set(factor.evidence_ids) - evidence.keys() or set(factor.source_selection_ids) - accepted_sources.keys():
                    reject("factor", factor.factor_id, "包含未知/已取代/来源排除的事实、已排除材料或未通过定位的来源，未作为分析因素采用。")
                    continue
                pending = [key for key in unique(factor.fact_ids) if key not in confirmed]
                limits = ["有利/不利方向与描述由调用方提出，仅核对引用，不证明其成立或证明力。"]
                if pending:
                    limits.append("部分前提尚未确认或存在冲突，不作为已成立事实。")
                if not factor.source_selection_ids:
                    limits.append("未绑定法律或案例来源；不能单凭该因素推出法律结论。")
                factors[factor.factor_id] = OutcomeFactorReview(candidate=factor,
                    status="premises_pending" if pending else "references_checked",
                    confirmed_fact_ids=[key for key in unique(factor.fact_ids) if key in confirmed],
                    pending_fact_ids=pending, limitations=limits)
            for comparison in request.case_comparisons:
                card = accepted_sources.get(comparison.source_selection_id)
                if card is None or card.source_type != "case" or set(comparison.fact_ids) - active_facts.keys():
                    reject("case_comparison", comparison.comparison_id, "需已核对的案例来源和本案有效事实；法条不能代替案例。")
                    continue
                limits = ["相似点和差异由调用方提供，未验证语义；历史案例结果不能直接等同本案结果。"]
                if not comparison.differences or any(not text.strip() for text in comparison.differences):
                    limits.append("类案差异尚未完整说明，不能以表面相似认定结果相同。")
                if any(key not in confirmed for key in comparison.fact_ids):
                    limits.append("用于比较的本案事实仍有待确认或冲突。")
                comparisons[comparison.comparison_id] = OutcomeCaseReview(candidate=comparison, limitations=limits)
            for scenario in request.scenarios:
                matched = [factors[key] for key in scenario.factor_ids if key in factors]
                invalid = len(matched) != len(scenario.factor_ids) or any(item.candidate.claim_id != scenario.claim_id for item in matched)
                invalid = invalid or bool(set(scenario.source_selection_ids) - accepted_sources.keys())
                if scenario.kind != "undetermined" and not any(
                    accepted_sources[key].source_type == "statute" for key in scenario.source_selection_ids if key in accepted_sources):
                    invalid = True
                if invalid:
                    reject("scenario", scenario.scenario_id, "条件分支需同一诉求的有效因素及可核对来源；支持/部分支持/不支持候选还需法律来源。")
                    continue
                scenarios[scenario.scenario_id] = ConditionalOutcome(scenario_id=scenario.scenario_id, kind=scenario.kind,
                    origin="caller", status="premises_pending" if any(item.pending_fact_ids for item in matched) else "candidate_needs_review",
                    statement=scenario.statement, conditions=scenario.conditions, factor_ids=unique(scenario.factor_ids),
                    source_selection_ids=unique(scenario.source_selection_ids))
            claims, links, refs = [], [], []
            for claim_id in chosen:
                claim = active_claims[claim_id]
                claim_factors = [item for item in factors.values() if item.candidate.claim_id == claim_id]
                claim_comparisons = [item for item in comparisons.values() if item.candidate.claim_id == claim_id]
                scenario_ids = {item.scenario_id for item in request.scenarios if item.claim_id == claim_id}
                claim_scenarios = [item for key, item in scenarios.items() if key in scenario_ids]
                related_facts = unique(claim.fact_ids + [key for item in claim_factors for key in item.candidate.fact_ids]
                                       + [key for item in claim_comparisons for key in item.candidate.fact_ids])
                related_evidence = unique(claim.evidence_ids + [key for item in claim_factors for key in item.candidate.evidence_ids])
                finding_ids = []
                for finding in context.findings:
                    specified_claims = {field.split(".")[1] for field in finding.affected_fields if field.startswith("claims.")}
                    if claim_id in specified_claims or not specified_claims and (
                        not finding.fact_ids and not finding.evidence_ids or set(finding.fact_ids) & set(related_facts)
                        or set(finding.evidence_ids) & set(related_evidence)):
                        finding_ids.append(finding.finding_id)
                unresolved = ["证据真实性、证明力、责任分配、法律适用及条件是否满足尚未审查；不输出支持金额或胜诉概率。"]
                if claim.status != "user_confirmed":
                    unresolved.append("该诉求尚未由用户明确确认。")
                pending = [active_facts[key] for key in related_facts if key in active_facts and key not in confirmed]
                if pending or finding_ids:
                    unresolved.append("存在待核对前提或证据问题，请逐项处理对应事实与Finding。")
                if any(key not in active_facts for key in claim.fact_ids):
                    unresolved.append("诉求仍引用已取代或来源被排除的事实，需要更新关联。")
                if not any(item.candidate.polarity == "adverse" for item in claim_factors):
                    unresolved.append("尚未输入明确的不利因素，不代表不存在不利材料或抗辩。")
                if not claim_comparisons:
                    unresolved.append("尚无已整理的类案差异，不能将其他案件的裁判直接用于本案。")
                if not claim_scenarios:
                    unresolved.append("未取得可核对的专项结果分析，仅提供未应用于本案的条件分支框架。")
                selected_sources = unique([key for item in claim_factors for key in item.candidate.source_selection_ids]
                    + [item.candidate.source_selection_id for item in claim_comparisons]
                    + [key for item in claim_scenarios for key in item.source_selection_ids])
                if not any(accepted_sources[key].source_type == "statute" for key in selected_sources):
                    unresolved.append("该诉求尚无明确绑定的法律原文，未形成确定法律结论。")
                claim_refs = unique([accepted_sources[key].source_ref for key in selected_sources])
                refs += claim_refs
                links.append(OutputLink(output_location="claims:" + claim_id,
                    fact_ids=[key for key in related_facts if key in confirmed],
                    evidence_ids=[key for key in related_evidence if key in evidence], source_refs=claim_refs))
                claims.append(ClaimOutcome(claim=claim,
                    confirmed_facts=[active_facts[key] for key in related_facts if key in confirmed], pending_facts=pending,
                    available_evidence=[evidence[key] for key in related_evidence if key in evidence],
                    excluded_evidence_ids=[key for key in related_evidence if key not in evidence], finding_ids=unique(finding_ids),
                    factors=claim_factors, case_comparisons=claim_comparisons,
                    scenarios=_templates(claim_id) + claim_scenarios, unresolved_items=unresolved))
            deadline(request)
            performed = ["case_claim_selection", "active_fact_and_evidence_ids", "confirmation_and_conflicts"]
            if request.source_selections and self.source_reviewer is not None:
                performed.append("source_identity_version_quote")
            if request.factors or request.scenarios or request.case_comparisons:
                performed.append("candidate_reference_consistency")
            if request.case_comparisons:
                performed.append("case_comparison_source_kind")
            data = OutcomeAnalysisData(claims=claims, findings=context.findings, sources=source_checks,
                rejected_candidates=rejected, output_links=links,
                checks_performed=performed,
                checks_not_performed=["legal_applicability", "semantic_entailment", "evidence_authenticity_and_weight",
                                      "adverse_factor_completeness", "case_similarity_semantics", "court_outcome_prediction"],
                limitations=context.limitations + source_issues + [
                    "本模块整理成员四/用户提供的候选分析，不调用LLM、不自动生成具体法律推断。",
                    "默认三个分支为条件化讨论框架，template_not_applied明确表示未认定本案满足条件。",
                    "有利与不利因素均保留调用方方向及引用；引用存在不等于支持关系已成立。",
                    "诉求amount仅为请求金额，不是法院支持金额；没有金额预测、比例评分或胜诉概率字段。",
                    "未绑定到因素/分支/类案比较的来源卡片仅供参考，不加入本分析的结论引用。",
                ])
            return BusinessResult[OutcomeAnalysisData](case_id=case_id, case_version=version,
                status="insufficient" if claims else "empty", summary="已按诉求整理条件分支、材料前提和未核验事项。" if claims else "当前没有可分析的有效诉求。",
                data=data, source_refs=unique(refs))
        except (ValidationError, ValueError, KeyError, TimeoutError) as exc:
            return failure(case_id, version, exc)
