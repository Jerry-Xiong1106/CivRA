"""Structured records for Member 1's timeline and evidence graph, not fabricated charts."""

from pydantic import ValidationError
from datetime import datetime, timezone

from .contracts import VisualizationData, VisualizationRequest
from .models import BusinessError, BusinessResult
from .evidence import check_evidence
from .business_support import deadline


def build_visualization(request,*,evidence_checker=None):
    case_id,version=None,None
    try:
        request=VisualizationRequest.model_validate(request.model_dump(mode="json") if isinstance(request,VisualizationRequest) else request)
        snapshot=request.case_snapshot
        case_id,version=snapshot.case_id,snapshot.case_version
        if request.context.deadline_at and datetime.now(timezone.utc)>=request.context.deadline_at:
            return BusinessResult[VisualizationData](case_id=case_id,case_version=version,status="error",data=None,
                summary="展示请求已过期。",error=BusinessError(code="timeout",message="展示请求已过期。"))
        records,links=[],[]
        report=(evidence_checker or check_evidence)(dict(context=request.context,case_version=version,case_snapshot=snapshot))
        if report.status=="error": return report
        conflicts={f.fact_id:[item.finding_id for item in report.data.conflicts if f.fact_id in item.fact_ids] for f in snapshot.facts}
        missing=0
        if request.chart_type=="timeline":
            for fact in snapshot.facts:
                if fact.value_type!="date":
                    continue
                if fact.value is None:
                    missing+=1
                    continue
                records.append({"id":fact.fact_id,"date":fact.value,"field":fact.field,
                    "confirmation_status":fact.confirmation_status,"superseded_by":fact.superseded_by,
                    "active":fact.superseded_by is None,"conflict_finding_ids":conflicts[fact.fact_id],"evidence_ids":fact.evidence_ids,
                    "source_refs":[p.model_dump(mode="json") for p in fact.source_refs],
                    "material_refs":[ref.model_dump(mode="json") for ref in fact.material_refs],"statement_refs":fact.statement_refs})
            records.sort(key=lambda record:(record["date"],record["id"]))
        else:
            for fact in snapshot.facts:
                records.append({"id":"fact:"+fact.fact_id,"type":"fact","label":fact.field,
                    "status":fact.confirmation_status,"superseded_by":fact.superseded_by,"value":fact.value,
                    "active":fact.superseded_by is None,"conflict_finding_ids":conflicts[fact.fact_id],
                    "material_refs":[r.model_dump(mode="json") for r in fact.material_refs],"statement_refs":fact.statement_refs})
                if fact.superseded_by:
                    links.append({"from":"fact:"+fact.fact_id,"to":"fact:"+fact.superseded_by,"type":"superseded_by"})
                for evidence_id in fact.evidence_ids:
                    links.append({"from":"evidence:"+evidence_id,"to":"fact:"+fact.fact_id,"type":"records"})
            for evidence in snapshot.evidence:
                records.append({"id":"evidence:"+evidence.evidence_id,"type":"evidence","label":evidence.file_name,
                    "status":evidence.status,"material_refs":[ref.model_dump(mode="json") for ref in evidence.material_refs]})
                links += [{"from":"evidence:"+evidence.evidence_id,"to":"fact:"+fid,"type":"asserted_proof"} for fid in evidence.proves_fact_ids]
                if evidence.duplicate_of:
                    links.append({"from":"evidence:"+evidence.evidence_id,"to":"evidence:"+evidence.duplicate_of,"type":"duplicate_of"})
            for claim in snapshot.claims:
                records.append({"id":"claim:"+claim.claim_id,"type":"claim","label":claim.description,"status":claim.status})
                links += [{"from":"fact:"+fact_id,"to":"claim:"+claim.claim_id,"type":"linked_to"} for fact_id in claim.fact_ids]
                links += [{"from":"evidence:"+eid,"to":"claim:"+claim.claim_id,"type":"claim_material"} for eid in claim.evidence_ids]
            links=list({(x["from"],x["to"],x["type"]):x for x in links}.values())
            nodes={r["id"]:r for r in records}
            for link in links:
                link["requires_review"]=any(nodes[key].get("status") in {"excluded","unconfirmed","needs_confirmation","proposed","withdrawn"} or
                    nodes[key].get("conflict_finding_ids") or nodes[key].get("superseded_by") for key in (link["from"],link["to"]))
                link["legally_proven"]=False
        deadline(request)
        return BusinessResult[VisualizationData](case_id=case_id,case_version=version,status="ok",summary="已生成当前案件的结构化展示数据。",
            data=VisualizationData(chart_type=request.chart_type,records=records,links=links,sample_size=len(records),
                missing_values=missing,scope="单个案件现有记录；包含未确认及已取代记录，不代表司法认定或统计样本。"))
    except TimeoutError:
        return BusinessResult[VisualizationData](case_id=case_id,case_version=version,status="error",data=None,summary="展示请求已过期。",
            error=BusinessError(code="timeout",message="visualization deadline expired"))
    except (ValidationError,ValueError) as exc:
        return BusinessResult[VisualizationData](case_id=case_id,case_version=version,status="error",summary="展示请求不合法。",data=None,
            error=BusinessError(code="invalid_request",message=str(exc).splitlines()[0]))
