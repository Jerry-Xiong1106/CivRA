"""Deterministic evidence consistency checks, not legal admissibility decisions."""

from __future__ import annotations

import hashlib
import json
from collections import defaultdict
from datetime import datetime, timezone

from pydantic import ValidationError

from .contracts import BuildEvidenceDirectoryRequest, CheckEvidenceData, CheckEvidenceRequest, EvidenceDirectoryData, EvidenceDirectoryEntry
from ..models import BusinessError, BusinessResult, Fact, Finding, SuggestedQuestion
from ..case_context import active_facts, has_known_value
from .evidence_rules import load_evidence_rules, profile_for
from .evidence_similarity import inspect_text_similarity

FIELD_LABELS = {
    "accident_date": "事故发生日期", "insurance": "保险关系",
    "liability_finding": "责任认定信息", "repair_amount": "维修金额", "claimant_name": "当事人姓名",
    "defendant_name": "对方姓名或名称", "accident_location": "事故地点", "medical_amount": "医疗金额",
    "towing_amount": "拖车金额", "appraisal_amount": "鉴定金额",
}


def _label(field):
    return FIELD_LABELS.get(field, FIELD_LABELS.get(field.rsplit(".", 1)[-1], field))


def _id(prefix, *values):
    raw = json.dumps(values, ensure_ascii=False, sort_keys=True, default=str).encode("utf-8")
    return prefix + "_" + hashlib.sha256(raw).hexdigest()[:16]


def _canonical(fact: Fact):
    value = fact.value
    if fact.value_type == "money":
        # Decimal.normalize() can round long amounts under the default context.
        amount = value["amount"]
        value = {"amount": amount.rstrip("0").rstrip(".") if "." in amount else amount, "currency": value["currency"]}
    return json.dumps([fact.value_type, value], ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _unique(items):
    result = []
    seen = set()
    for item in items:
        key = item.model_dump_json() if hasattr(item, "model_dump_json") else item
        if key not in seen:
            seen.add(key)
            result.append(item)
    return result


def _inspect(snapshot, *, rules=None, text_reader=None, deadline_check=None) -> CheckEvidenceData:
    rules = rules or load_evidence_rules()
    profile = profile_for(snapshot.dispute_type, rules)
    evidence = {item.evidence_id: item for item in snapshot.evidence if item.status != "excluded"}
    facts = active_facts(snapshot)
    by_field = defaultdict(list)
    for fact in facts:
        by_field[fact.field].append(fact)
    missing, conflicts, duplicates, gaps, questions = [], [], [], [], []
    required_fields = dict(profile.required_fields)
    if profile.profile_id == "private_lending_v1" and any(f.value is True for f in by_field["has_repaid"] if f.value_type == "boolean"):
        required_fields["repaid_principal"] = "明确用于归还本笔借款本金的累计金额"
    for field, label in required_fields.items():
        known = [fact for fact in by_field[field] if has_known_value(fact)]
        if not known or all(fact.confirmation_status == "disputed" for fact in known):
            candidates = by_field[field]
            message = f"缺少可用于当前分析的{label}，请补充或确认。"
            finding = Finding(finding_id=_id("missing", field), category="missing_fact", severity="warning",
                message=message, affected_fields=[field], fact_ids=[fact.fact_id for fact in candidates],
                evidence_ids=sorted({key for fact in candidates for key in fact.evidence_ids if key in evidence}),
                material_refs=_unique([ref for fact in candidates for ref in fact.material_refs]),
                suggested_correction="补充该字段及其来源；未知时保留未知。")
            missing.append(finding)
            questions.append(SuggestedQuestion(question_id=_id("q_missing", field),
                text=f"请说明{label}；如果不知道，请明确说明。",
                field=field, reason=message, fact_ids=finding.fact_ids, evidence_ids=finding.evidence_ids))
    for field, candidates in sorted(by_field.items()):
        known = [fact for fact in candidates if has_known_value(fact)]
        if len({_canonical(fact) for fact in known}) > 1:
            refs = _unique([ref for fact in known for ref in fact.material_refs])
            ids = sorted({key for fact in known for key in fact.evidence_ids if key in evidence})
            fact_ids = sorted(fact.fact_id for fact in known)
            message = f"{_label(field)}有不一致的记载，尚未明确解决。"
            finding = Finding(finding_id=_id("conflict", field, fact_ids), category="conflict", severity="warning",
                message=message, affected_fields=[field], fact_ids=fact_ids, evidence_ids=ids,
                material_refs=refs, source_refs=_unique([ref for fact in known for ref in fact.source_refs]),
                suggested_correction="向用户核实；保留原文，以明确确认的新事实及 superseded_by 记录解决关系。")
            conflicts.append(finding)
            questions.append(SuggestedQuestion(question_id=_id("q_conflict", field, fact_ids),
                text=f"请核实{_label(field)}的不同记载，说明采用哪个值以及原因。",
                field=field, reason=message, fact_ids=fact_ids, evidence_ids=ids))
    groups = defaultdict(list)
    for item in evidence.values():
        groups[item.content_sha256].append(item)
    for digest, group in sorted(groups.items()):
        if len(group) < 2:
            continue
        ids = sorted(item.evidence_id for item in group)
        message = "材料登记的字节哈希相同，可能为同一文件重复上传；请确认后处理，勿重复计入金额。"
        duplicates.append(Finding(finding_id=_id("duplicate", digest, ids), category="duplicate", severity="warning",
            message=message, evidence_ids=ids, material_refs=_unique([ref for item in group for ref in item.material_refs]),
            suggested_correction="确认保留哪一份；其余标为 excluded 并保存确认记录，不删除原材料。"))
        questions.append(SuggestedQuestion(question_id=_id("q_duplicate", ids), text="这些文件是否为同一材料？请确认保留哪一份。",
            field="evidence.duplicate", reason=message, evidence_ids=ids))
    active_ids = {fact.fact_id for fact in facts}
    for item in evidence.values():
        forward = {fact.fact_id for fact in facts if item.evidence_id in fact.evidence_ids}
        reverse = set(item.proves_fact_ids) & active_ids
        mismatch = sorted(forward ^ reverse)
        if mismatch:
            gaps.append(Finding(finding_id=_id("evidence_fact_links", item.evidence_id, mismatch),
                category="missing_evidence", severity="warning", message="材料与事实的双向关联登记不一致，需要核对拟证明事项。",
                affected_fields=[f"evidence.{item.evidence_id}.proves_fact_ids"], fact_ids=mismatch,
                evidence_ids=[item.evidence_id], material_refs=item.material_refs,
                suggested_correction="核对原文后补齐材料的proves_fact_ids；若需改变已存事实的来源关联，须新增事实ID并显式确认取代，再更新诉求引用，不直接改写原事实。"))
    for claim in snapshot.claims:
        if claim.status == "withdrawn":
            continue
        linked = [evidence[key] for key in claim.evidence_ids if key in evidence]
        linked_facts = [fact for fact in facts if fact.fact_id in claim.fact_ids and has_known_value(fact)
                        and fact.confirmation_status != "disputed"]
        if not linked_facts:
            gaps.append(Finding(finding_id=_id("claim_facts", claim.claim_id), category="missing_evidence", severity="warning",
                message=f"诉求“{claim.description}”尚未关联可用事实。", affected_fields=[f"claims.{claim.claim_id}.fact_ids"],
                suggested_correction="关联能够说明该诉求的事实及来源，不能仅补造文字。"))
        if not linked:
            gaps.append(Finding(finding_id=_id("claim_evidence", claim.claim_id), category="missing_evidence", severity="warning",
                message=f"诉求“{claim.description}”尚未关联未排除的材料。", affected_fields=[f"claims.{claim.claim_id}.evidence_ids"],
                fact_ids=claim.fact_ids, suggested_correction="补充或关联材料；暂缺时在输出中说明。"))
        linked_ids = {item.evidence_id for item in linked}
        if linked and linked_facts and not any(linked_ids & set(fact.evidence_ids) for fact in linked_facts):
            gaps.append(Finding(finding_id=_id("claim_disconnected", claim.claim_id), category="missing_evidence", severity="warning",
                message=f"诉求“{claim.description}”关联的事实与材料尚未建立明确对应关系。",
                affected_fields=[f"claims.{claim.claim_id}.fact_ids", f"claims.{claim.claim_id}.evidence_ids"],
                fact_ids=[fact.fact_id for fact in linked_facts], evidence_ids=sorted(linked_ids),
                material_refs=_unique([ref for item in linked for ref in item.material_refs]),
                suggested_correction="核对哪些材料支持哪些事实，再明确关联；仅同时列出事实和文件不代表已有证明关系。"))
        for requirement in profile.claim_materials.get(claim.kind, ()):
            if not any(item.document_type in requirement.any_of for item in linked):
                available = sorted((item for item in evidence.values() if item.document_type in requirement.any_of
                                    and item.evidence_id not in linked_ids), key=lambda item: item.evidence_id)
                if available:
                    message = f"诉求“{claim.description}”未关联{requirement.label}；案件中已有此类型材料，请先核对是否相关，无需立即重复上传。"
                    correction = f"{requirement.reason}核对已有材料是否与该诉求相关，再明确关联或解释不适用；不自动关联。"
                else:
                    message = f"诉求“{claim.description}”未关联{requirement.label}，请补充或解释暂缺情况。"
                    correction = f"{requirement.reason}补充对应材料或解释暂缺情况；该提示是开发规则，不是法定材料清单。"
                gaps.append(Finding(finding_id=_id("claim_document", claim.claim_id, requirement.requirement_id),
                    category="missing_evidence", severity="warning",
                    message=message, affected_fields=[f"claims.{claim.claim_id}.evidence_ids"],
                    evidence_ids=[item.evidence_id for item in available] or [item.evidence_id for item in linked],
                    material_refs=_unique([ref for item in available for ref in item.material_refs]),
                    suggested_correction=correction))
    for gap in gaps:
        questions.append(SuggestedQuestion(question_id=_id("q_gap", gap.finding_id), text=gap.message + "请补充或说明。",
            field=gap.affected_fields[0], reason=gap.message, fact_ids=gap.fact_ids, evidence_ids=gap.evidence_ids))
    similar, similar_questions, similarity_checks, similarity_limits = inspect_text_similarity(
        list(evidence.values()), text_reader, deadline_check=deadline_check)
    duplicates.extend(similar)
    questions.extend(similar_questions)
    unconfigured = sorted({claim.kind for claim in snapshot.claims if claim.status != "withdrawn"
                           and claim.kind not in profile.claim_materials})
    limits = [f"未配置诉求类型{kind}的专门材料规则，仅检查已登记关联。" for kind in unconfigured]
    return CheckEvidenceData(missing_facts=missing, conflicts=conflicts, duplicates=duplicates,
        material_gaps=gaps, suggested_questions=questions,
        rule_profile=profile.profile_id,
        checks_performed=["required_profile_fields", "single_field_value_conflicts", "exact_file_duplicates", "claim_material_links",
                          "evidence_fact_link_consistency", *similarity_checks],
        limitations=[f"开发规则集版本：{rules.rule_set_version}；本次不判断材料真实性、法院采纳和法律证明力。",
                     "必需字段及材料类型来自版本化开发规则，不是法定起诉条件或完整法律清单。",
                     "未知纠纷和诉求只做通用一致性检查，不宣称覆盖该领域的材料要求。",
                     "不同文件的金额字段保留各自范围，不因金额或日期相同就合并，也不将不同票据金额直接判为冲突。",
                     *limits, *similarity_limits])


def check_evidence(request: CheckEvidenceRequest | dict, *, rules=None, text_reader=None) -> BusinessResult[CheckEvidenceData]:
    case_id, version = None, None
    try:
        request = CheckEvidenceRequest.model_validate(request.model_dump(mode="json") if isinstance(request, CheckEvidenceRequest) else request)
        case_id, version = request.context.case_id, request.case_version
        if request.context.deadline_at is not None and datetime.now(timezone.utc) >= request.context.deadline_at:
            return _failure(case_id, version, "timeout", "证据检查已超过截止时间。")
        def deadline_check():
            if request.context.deadline_at and datetime.now(timezone.utc) >= request.context.deadline_at:
                raise TimeoutError("证据检查已超过截止时间。")
        report = _inspect(request.case_snapshot, rules=rules,
                          text_reader=(lambda item: text_reader(case_id, item)) if text_reader else None,
                          deadline_check=deadline_check)
        deadline_check()
        count = sum(len(items) for items in [report.missing_facts, report.conflicts, report.duplicates, report.material_gaps])
        return BusinessResult[CheckEvidenceData](case_id=case_id, case_version=version,
            status="insufficient" if count else "ok",
            summary=f"规则检查发现 {count} 项待处理问题；未判断法律证明力。" if count else "已执行的规则未发现待处理问题；未覆盖范围见limitations，仍需复核。",
            data=report, source_refs=_unique([ref for item in report.conflicts for ref in item.source_refs]))
    except TimeoutError:
        return _failure(case_id, version, "timeout", "证据检查已超过截止时间。")
    except (ValidationError, ValueError) as exc:
        return _failure(case_id, version, "invalid_request", "证据检查请求不合法。", {"reason": str(exc).splitlines()[0]})


def build_evidence_directory(request: BuildEvidenceDirectoryRequest | dict, *, rules=None, text_reader=None) -> BusinessResult[EvidenceDirectoryData]:
    case_id, version = None, None
    try:
        request = BuildEvidenceDirectoryRequest.model_validate(request.model_dump(mode="json") if isinstance(request, BuildEvidenceDirectoryRequest) else request)
        snapshot = request.case_snapshot
        case_id, version = snapshot.case_id, snapshot.case_version
        if request.context.deadline_at is not None and datetime.now(timezone.utc) >= request.context.deadline_at:
            return _failure(case_id, version, "timeout", "证据目录生成已超过截止时间。")
        if snapshot.stage != "litigation" or snapshot.selected_route != "litigation":
            return _failure(case_id, version, "invalid_request", "证据目录仅在用户选择诉讼准备后生成。")
        def deadline_check():
            if request.context.deadline_at and datetime.now(timezone.utc) >= request.context.deadline_at:
                raise TimeoutError("证据目录生成已超过截止时间。")
        report = _inspect(snapshot, rules=rules,
                          text_reader=(lambda item: text_reader(case_id, item)) if text_reader else None,
                          deadline_check=deadline_check)
        findings = report.missing_facts + report.conflicts + report.duplicates + report.material_gaps
        entries = []
        unresolved_entries = False
        for evidence in snapshot.evidence:
            deadline_check()
            if evidence.status == "excluded":
                continue
            facts = [fact for fact in snapshot.facts if evidence.evidence_id in fact.evidence_ids or fact.fact_id in evidence.proves_fact_ids]
            claims = [claim for claim in snapshot.claims if claim.status != "withdrawn" and evidence.evidence_id in claim.evidence_ids]
            warnings = list(evidence.warnings)
            warnings += [finding.message for finding in findings if evidence.evidence_id in finding.evidence_ids
                         or set(finding.fact_ids) & {fact.fact_id for fact in facts}]
            if any(not fact.superseded_by and (fact.confirmation_status != "confirmed" or not has_known_value(fact)) for fact in facts):
                warnings.append("相关事实尚有未确认内容；证明事项仅供人工复核。")
                unresolved_entries = True
            if evidence.status == "needs_confirmation":
                warnings.append("材料状态为待核对，请核对原图和内容。")
                unresolved_entries = True
            for fact in facts:
                if fact.superseded_by:
                    warnings.append(f"事实 {fact.fact_id} 的原始记载已被确认事实 {fact.superseded_by} 取代；原文差异仍需人工核对。")
            purposes = [f"拟说明{_label(fact.field)}（事实 {fact.fact_id}，"
                        f"{'原记载，已被 ' + fact.superseded_by + ' 取代' if fact.superseded_by else fact.confirmation_status}）。" for fact in facts]
            if not purposes:
                purposes = ["尚未关联明确证明事项，请人工补充。"]
                warnings.append("尚未登记关联事实，不能自动生成确定的证明事项。")
                unresolved_entries = True
            entries.append(EvidenceDirectoryEntry(number=len(entries)+1, evidence_id=evidence.evidence_id,
                file_id=evidence.file_id, file_version=evidence.file_version, file_name=evidence.file_name,
                document_type=evidence.document_type, material_refs=evidence.material_refs,
                fact_ids=[fact.fact_id for fact in facts], claim_ids=[claim.claim_id for claim in claims],
                proof_purposes=purposes, review_status="needs_confirmation" if warnings else evidence.status,
                warnings=_unique(warnings)))
        return BusinessResult[EvidenceDirectoryData](case_id=case_id, case_version=version,
            status="insufficient" if findings or unresolved_entries else "ok", summary=f"已生成 {len(entries)} 项诉讼证据目录草稿及 {len(findings)} 项规则问题；还需核对各项warnings。",
            data=EvidenceDirectoryData(entries=entries,
                excluded_evidence_ids=[item.evidence_id for item in snapshot.evidence if item.status == "excluded"],
                supplementary_checklist=findings, suggested_questions=report.suggested_questions,
                rule_profile=report.rule_profile, limitations=report.limitations),
            source_refs=_unique([ref for finding in findings for ref in finding.source_refs]))
    except TimeoutError:
        return _failure(case_id, version, "timeout", "证据目录生成已超过截止时间。")
    except (ValidationError, ValueError) as exc:
        return _failure(case_id, version, "invalid_request", "证据目录请求不合法。", {"reason": str(exc).splitlines()[0]})


def _failure(case_id, version, code, message, details=None):
    return BusinessResult(case_id=case_id, case_version=version, status="error", summary=message, data=None,
                          error=BusinessError(code=code, message=message, details=details))
