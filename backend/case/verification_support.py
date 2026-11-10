"""Pinned source checks and bounded, non-executing correction tasks."""
from copy import deepcopy
import json

from .business_support import deadline, unique, review_sources
from .models import Finding
from .research import CaseResearchService, SourceSelection


def validate_semantic(findings,usage,case):
    if not isinstance(findings,list) or len(findings)>100:
        raise ValueError("semantic review must return at most 100 findings")
    parsed=[Finding.model_validate(f.model_dump(mode="json") if isinstance(f,Finding) else f) for f in findings]
    facts={f.fact_id for f in case.facts}; evidence={e.evidence_id for e in case.evidence}
    if len({f.finding_id for f in parsed})!=len(parsed):
        raise ValueError("duplicate semantic finding IDs")
    for f in parsed:
        if f.category not in {"conflict","citation"} or set(f.fact_ids)-facts or set(f.evidence_ids)-evidence or f.source_refs or f.material_refs:
            raise ValueError("semantic review used unvalidated references")
        if len(f.message)>6000 or len(f.suggested_correction or "")>6000:
            raise ValueError("semantic finding too large")
    if usage is not None and not isinstance(usage,dict):
        raise ValueError("model usage must be an object or null")
    json.dumps(usage,allow_nan=False)
    return deepcopy(parsed),deepcopy(usage)


def resolve_review_sources(request,refs,resolver,repository):
    cache,selections,rejected={},[],[]
    for pointer in unique(refs):
        deadline(request)
        try:
            if resolver is None or pointer.source_version is None:
                raise ValueError("缺少读取器或明确版本")
            raw=deepcopy(dict(resolver(pointer.model_copy(deep=True))))
            cache[(pointer.source_id,pointer.source_version)]=raw
            selections.append(SourceSelection(source_ref=pointer,expected_kind="case" if raw.get("source_type")=="case" else "statute"))
        except TimeoutError:
            raise
        except Exception:
            rejected.append(dict(source_ref=pointer.model_dump(mode="json"),state="rejected",reason="原文读取失败或版本不明确"))
    if repository is None:
        # Used only by standalone verifier instances; still enforce the same reader checks.
        class SnapshotReader:
            def get_case(self,case_id):
                if case_id!=request.context.case_id: raise ValueError("case mismatch")
                return request.case_snapshot.model_copy(deep=True)
        repository=SnapshotReader()
    reader=CaseResearchService(repository,source_resolver=lambda p:cache[(p.source_id,p.source_version)])
    cards,issues=review_sources(request,selections,reader.review_sources)
    accepted=list(cards.values())
    accepted_keys={(c.source_ref.source_id,c.source_ref.source_version) for c in accepted}
    checks=[dict(source_ref=c.source_ref.model_dump(mode="json"),state="located",warnings=c.warnings,
        text_sha256=c.text_sha256,locator=c.locator,semantic_support_checked=False) for c in accepted]
    for selection in selections:
        p=selection.source_ref
        if (p.source_id,p.source_version) not in accepted_keys:
            rejected.append(dict(source_ref=p.model_dump(mode="json"),state="rejected",reason="来源身份、版本、正文、标题或定位不合格"))
    return [cache[key] for key in sorted(accepted_keys)],checks+rejected,issues


def correction_tasks(findings,not_performed):
    actions={
        "version":("regenerate","读取最新案件版本并重新生成",["draft_document","verify_output"],"成员3/4"),
        "calculation":("recalculate","核对输入与规则后重新计算",["calculate_costs","verify_output"],"成员3/4"),
        "citation":("retrieve_sources","补充精确版本原文并复核支持关系",["review_research_sources","verify_output"],"成员2/4"),
        "required_field":("complete_fields","补齐或明确不适用字段，再生成草稿",["update_case","draft_document","verify_output"],"成员1/3/4"),
    }
    tasks=[]
    for f in findings:
        action,title,tools,owner=actions.get(f.category,("resolve_facts","核对原始材料及事实，取得真实用户确认后修正",["check_evidence","update_case","verify_output"],"成员3/4"))
        tasks.append(dict(task_id="repair:"+f.finding_id,finding_ids=[f.finding_id],action=action,title=title,
            owner=owner,suggested_interfaces=tools,output_location=f.output_location,fact_ids=f.fact_ids,evidence_ids=f.evidence_ids,
            suggested_correction=f.suggested_correction,executed=False))
    if not_performed:
        tasks.append(dict(task_id="repair:unperformed",finding_ids=[],action="complete_review",
            title="接通并完成尚未执行的检查",owner="成员2/4/5",checks=unique(not_performed),executed=False))
    return tasks
