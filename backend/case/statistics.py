"""Descriptive, source-located case sample statistics; never a success predictor."""
from collections import defaultdict
from datetime import date
from decimal import Decimal
from typing import Literal
import unicodedata

from pydantic import Field, model_validator
from .business_support import deadline, failure, review_sources
from .contracts import SnapshotRequest, VisualizationData
from .models import BusinessResult, ContractModel, Money
from .research import CaseResearchService, SourceSelection
from .evidence import UNKNOWN_TEXT

Dimension=Literal["year","court","court_region","accident_region","party_region","dispute_type","issue","outcome"]


class StatisticsFilters(ContractModel):
    court_region: str | None = Field(default=None,max_length=200)
    accident_region: str | None = Field(default=None,max_length=200)
    party_region: str | None = Field(default=None,max_length=200)
    dispute_type: str | None = Field(default=None,max_length=200)
    year_from: int | None = Field(default=None,ge=1000,le=9999,strict=True)
    year_to: int | None = Field(default=None,ge=1000,le=9999,strict=True)

    @model_validator(mode="after")
    def ordered(self):
        if self.year_from and self.year_to and self.year_from>self.year_to: raise ValueError("invalid year range")
        if any(isinstance(v,str) and not v.strip() for v in self.model_dump().values()): raise ValueError("filters must not be blank")
        return self


class CaseStatisticsRequest(SnapshotRequest):
    selections: list[SourceSelection] = Field(max_length=200)
    group_by: Dimension = "court_region"
    filters: StatisticsFilters = Field(default_factory=StatisticsFilters)

    @model_validator(mode="after")
    def cases_only(self):
        if any(s.expected_kind!="case" for s in self.selections): raise ValueError("statistics requires case sources")
        keys=[(s.source_ref.source_id,s.source_ref.source_version) for s in self.selections]
        if len(keys)!=len(set(keys)): raise ValueError("duplicate source selection")
        return self


class CaseStatisticsData(VisualizationData):
    group_by: str
    input_source_count: int
    eligible_before_filters: int
    known_values: int
    samples: list[dict]
    excluded: list[dict]
    duplicate_sources: list[dict]
    filters: dict
    observed_date_range: dict
    limitations: list[str]


def _annotated(raw,text,field):
    annotation=raw.get(field)
    if annotation is None: return None,None
    if not isinstance(annotation,dict) or set(annotation)!={"value","quote"}:
        return None,"字段标注需要value和quote"
    value,quote=annotation["value"],annotation["quote"]
    if not isinstance(value,str) or not value.strip() or len(value)>200 or not isinstance(quote,str) or not 0<len(quote)<=2000:
        return None,"字段标注格式不合法"
    if value.strip().lower() in UNKNOWN_TEXT | {"未载明","未记载","不明确","n/a"}:
        return None,"字段仍是未知值"
    if quote not in text or value not in quote:
        return None,"字段值或引文不能在原文逐字定位"
    return dict(value=value,quote=quote,start=text.index(quote),end=text.index(quote)+len(quote)),None


class CaseStatistics:
    def __init__(self,repository,source_resolver):
        self.repository,self.source_resolver=repository,source_resolver

    def build(self,request):
        case_id,version=None,None
        try:
            request=CaseStatisticsRequest.model_validate(request.model_dump(mode="json") if hasattr(request,"model_dump") else request)
            case_id,version=request.context.case_id,request.case_version
            cache,excluded={},[]
            for selection in request.selections:
                deadline(request)
                p=selection.source_ref
                try:
                    cache[(p.source_id,p.source_version)]=dict(self.source_resolver(p.model_copy(deep=True)))
                except TimeoutError: raise
                except Exception:
                    excluded.append(dict(source_ref=p.model_dump(mode="json"),reason="source_unavailable"))
            reader=CaseResearchService(self.repository,source_resolver=lambda p:cache[(p.source_id,p.source_version)])
            cards=[]
            for offset in range(0,len(request.selections),30):
                selected=request.selections[offset:offset+30]
                accepted,_=review_sources(request,selected,reader.review_sources)
                cards.extend(accepted.values())
                good={(c.source_ref.source_id,c.source_ref.source_version) for c in accepted.values()}
                for s in selected:
                    p=s.source_ref
                    if (p.source_id,p.source_version) not in good and (p.source_id,p.source_version) in cache:
                        excluded.append(dict(source_ref=p.model_dump(mode="json"),reason="source_identity_or_location_rejected"))
            versions=defaultdict(set)
            for s in request.selections: versions[s.source_ref.source_id].add(s.source_ref.source_version)
            candidates=[]
            for card in cards:
                deadline(request)
                p=card.source_ref;raw=cache[(p.source_id,p.source_version)];text=raw["text"]
                if len(versions[p.source_id])>1:
                    excluded.append(dict(source_ref=p.model_dump(mode="json"),reason="multiple_unresolved_versions"));continue
                annotated=raw.get("statistics",{})
                if not isinstance(annotated,dict): annotated={}
                fields,warnings={},[]
                for field in ["case_number","decision_date","court","court_region","accident_region","party_region","dispute_type","issue","outcome"]:
                    item,error=_annotated(annotated,text,field)
                    if error: warnings.append(field+": "+error)
                    if item:
                        if field=="decision_date":
                            try: date.fromisoformat(item["value"])
                            except ValueError:
                                warnings.append("decision_date: 需要ISO日期且与原文字面一致");continue
                        fields[field]=item
                if "case_number" not in fields:
                    excluded.append(dict(source_ref=p.model_dump(mode="json"),reason="missing_located_case_number"));continue
                number=unicodedata.normalize("NFKC",fields["case_number"]["value"])
                number="".join(number.split())
                group=raw.get("case_group_id")
                if group is None: group=number
                if not isinstance(group,str) or not group.strip() or len(group)>200:
                    excluded.append(dict(source_ref=p.model_dump(mode="json"),reason="invalid_case_group_id"));continue
                awards=[]
                entries=raw.get("awards",[])
                if not isinstance(entries,list) or len(entries)>50:
                    warnings.append("awards: 金额条目格式不合法");entries=[]
                for entry in entries:
                    try:
                        if not isinstance(entry,dict) or set(entry)!={"loss_type","amount","currency","quote"}: raise ValueError()
                        amount=Money(amount=entry["amount"],currency=entry["currency"])
                        quote=entry["quote"];loss=entry["loss_type"]
                        if not isinstance(quote,str) or not 0<len(quote)<=2000 or not isinstance(loss,str) or not loss.strip() or len(loss)>200: raise ValueError()
                        if quote not in text or amount.amount not in quote or loss not in quote: raise ValueError()
                        awards.append(dict(loss_type=loss,amount=amount.model_dump(),quote=quote,start=text.index(quote)))
                    except (ValueError,TypeError,KeyError): warnings.append("awards: 某金额条目无法定位，未采用")
                candidates.append(dict(case_number=number,case_group_id=group,fields=fields,awards=awards,
                    source_refs=[p.model_dump(mode="json")],source_checks=[card.model_dump(mode="json")],warnings=warnings,
                    semantic_metadata_checked=False))
            groups=defaultdict(list)
            numbers=defaultdict(set)
            for sample in candidates: numbers[sample["case_number"]].add(sample["case_group_id"])
            for sample in candidates:
                if len(numbers[sample["case_number"]])>1:
                    excluded.append(dict(case_number=sample["case_number"],source_refs=sample["source_refs"],reason="conflicting_case_group_ids"))
                else:
                    groups[sample["case_group_id"]].append(sample)
            independent,duplicates=[],[]
            for group,items in groups.items():
                signature=lambda s:({k:v["value"] for k,v in s["fields"].items()},s["awards"])
                if any(signature(s)!=signature(items[0]) for s in items[1:]):
                    excluded.append(dict(case_group_id=group,source_refs=[p for s in items for p in s["source_refs"]],reason="related_case_records_disagree_select_one_record"));continue
                chosen=items[0]
                for other in items[1:]:
                    chosen["source_refs"]+=other["source_refs"];chosen["source_checks"]+=other["source_checks"]
                    duplicates.append(dict(case_group_id=group,source_refs=other["source_refs"],reason="same_case_group_collapsed"))
                independent.append(chosen)
            accepted=[]
            for sample in independent:
                values={k:v["value"] for k,v in sample["fields"].items()}
                values["year"]=int(values["decision_date"][:4]) if values.get("decision_date") else None
                rejected=False
                for key,wanted in request.filters.model_dump().items():
                    if wanted is None:continue
                    actual=values.get("year" if key.startswith("year_") else key)
                    matches=actual is not None and (actual>=wanted if key=="year_from" else actual<=wanted if key=="year_to" else actual==wanted)
                    if not matches:
                        excluded.append(dict(case_group_id=sample["case_group_id"],reason="filter_missing" if actual is None else "filter_mismatch",field=key));rejected=True;break
                if not rejected:
                    sample["group_value"]=str(values[request.group_by]) if values.get(request.group_by) is not None else None
                    accepted.append(sample)
            counts=defaultdict(list)
            for sample in accepted: counts[sample["group_value"]].append(sample)
            records=[]
            for key,items in sorted(counts.items(),key=lambda pair:(pair[0] is None,pair[0] or "")):
                records.append(dict(value=key,label=key or "未知/未提供",count=len(items),denominator=len(accepted),
                    share_percent=str((Decimal(len(items))*100/Decimal(len(accepted))).quantize(Decimal("0.01"))),
                    case_group_ids=[s["case_group_id"] for s in items],source_refs=[p for s in items for p in s["source_refs"]]))
            dates=sorted(s["fields"]["decision_date"]["value"] for s in accepted if "decision_date" in s["fields"])
            missing=sum(s["group_value"] is None for s in accepted)
            data=CaseStatisticsData(chart_type="case_statistics",records=records,links=[],sample_size=len(accepted),missing_values=missing,
                scope="仅限本次选定且可定位的案例样本，按案件组去重；不是全部司法案件或胜诉概率。",group_by=request.group_by,
                input_source_count=len(request.selections),eligible_before_filters=len(independent),known_values=len(accepted)-missing,
                samples=accepted,excluded=excluded,duplicate_sources=duplicates,filters=request.filters.model_dump(),
                observed_date_range=dict(start=dates[0] if dates else None,end=dates[-1] if dates else None,missing=sum("decision_date" not in s["fields"] for s in accepted)),
                limitations=["地域按事故地、法院地、当事人所在地分别统计，不从法院名称推断地理位置。",
                    "统计字段与引文只完成逐字定位；字段含义、案件组关联和可比性仍需成员2/5核验。",
                    "同组记录不一致时暂不计入；未提供案件组时按定位案号去重，未知的关联审级仍可能造成重复。",
                    "金额按案件及损失项目展示，不合并不同币种或不同案情，不将请求额当裁判额。",
                    "各百分比分母为筛选后独立样本数，未知分组保留；四舍五入后合计可能不为100。"])
            deadline(request)
            return BusinessResult[CaseStatisticsData](case_id=case_id,case_version=version,status="empty" if not accepted else "insufficient",
                summary=f"整理{len(accepted)}个案件组，{missing}个分组字段未知；仅作样本描述。",data=data,
                source_refs=[c.source_ref for s in accepted for c in cards if c.source_ref.model_dump(mode="json") in s["source_refs"]])
        except (ValueError,TypeError,KeyError,TimeoutError) as exc:
            return failure(case_id,version,exc)
