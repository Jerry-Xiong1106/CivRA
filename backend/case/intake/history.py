"""Version comparison and resumption preparation over business state, not Agent checkpoints."""
from pydantic import Field
from ..business_support import deadline, failure
from ..contracts import CaseRequest, CaseAuditRecord
from ..models import BusinessResult, ContractModel, CaseSnapshot, PositiveInt
from ..errors import VersionConflict


class HistoryRequest(CaseRequest):
    after_version: int = Field(default=0,ge=0,strict=True)
    through_version: PositiveInt | None = None
    limit: int = Field(default=50,ge=1,le=200,strict=True)


class VersionDiffRequest(CaseRequest):
    from_version: PositiveInt
    to_version: PositiveInt


class ContinuationRequest(CaseRequest):
    since_version: PositiveInt | None = None


class HistoryData(ContractModel):
    items: list[CaseAuditRecord]
    through_version: int
    next_after_version: int | None


class VersionDiffData(ContractModel):
    from_version: int
    to_version: int
    changes: dict
    metadata_changes: dict
    substantive_changed: bool


class ContinuationData(ContractModel):
    case_snapshot: CaseSnapshot
    changes_since: VersionDiffData | None
    files: list[dict]
    unlinked_files: list[dict]
    outputs: list[dict]
    suggested_interfaces: list[str]
    inventory_truncated: bool
    limitations: list[str]


COLLECTIONS={"facts":"fact_id","evidence":"evidence_id","claims":"claim_id","statements":"message_id"}


def compare_versions(old,new):
    if old.case_id!=new.case_id or old.case_version>new.case_version:
        raise ValueError("versions must be from one case in chronological order")
    changes={}
    for name,key in COLLECTIONS.items():
        a={getattr(x,key):x.model_dump(mode="json") for x in getattr(old,name)}
        b={getattr(x,key):x.model_dump(mode="json") for x in getattr(new,name)}
        changes[name]=dict(added=[b[k] for k in sorted(b.keys()-a.keys())],removed=[a[k] for k in sorted(a.keys()-b.keys())],
            modified=[dict(id=k,before=a[k],after=b[k]) for k in sorted(a.keys()&b.keys()) if a[k]!=b[k]])
    metadata={key:dict(before=getattr(old,key),after=getattr(new,key)) for key in
        ("dispute_type","jurisdiction","stage","selected_route","user_goals") if getattr(old,key)!=getattr(new,key)}
    substantive=bool(metadata or any(any(changes[name].values()) for name in ("facts","evidence","claims")))
    return VersionDiffData(from_version=old.case_version,to_version=new.case_version,changes=changes,
        metadata_changes=metadata,substantive_changed=substantive)


def changed_ids(diff,name):
    group=diff.changes[name];key=COLLECTIONS[name]
    return {row[key] for row in group["added"]+group["removed"]}|{row["id"] for row in group["modified"]}


class CaseHistoryService:
    def __init__(self,repository,assets):
        self.repository,self.assets=repository,assets

    def _read(self,request):
        deadline(request)
        case=self.repository.get_case(request.context.case_id)
        if case.case_version!=request.case_version: raise VersionConflict(request.case_version,case.case_version)
        return case

    def history(self,request):
        return self._execute(request,HistoryRequest,self._history)

    def diff(self,request):
        return self._execute(request,VersionDiffRequest,self._diff)

    def continuation(self,request):
        return self._execute(request,ContinuationRequest,self._continuation)

    def _execute(self,request,model,operation):
        case_id,version=None,None
        try:
            request=model.model_validate(request.model_dump(mode="json") if hasattr(request,"model_dump") else request)
            case_id,version=request.context.case_id,request.case_version
            case=self._read(request)
            data=operation(request,case)
            self._read(request)
            status="insufficient" if isinstance(data,ContinuationData) and (data.unlinked_files or data.inventory_truncated or any(o["needs_review"] for o in data.outputs)) else "ok"
            return BusinessResult(case_id=case_id,case_version=version,status=status,data=data,
                summary="已读取案件保存记录与版本状态；未修改案件或自动重跑工具。")
        except (ValueError,KeyError,TypeError,TimeoutError) as exc:
            return failure(case_id,version,exc)

    def _history(self,request,case):
        if request.through_version is not None and request.through_version>case.case_version:
            raise ValueError("history boundary is newer than the case")
        return HistoryData(**self.repository.list_history(case.case_id,after_version=request.after_version,
            through_version=request.through_version,limit=request.limit))

    def _diff(self,request,case):
        if request.to_version>case.case_version: raise ValueError("cannot compare a future version")
        return compare_versions(self.repository.get_case(case.case_id,request.from_version),self.repository.get_case(case.case_id,request.to_version))

    def _continuation(self,request,case):
        if request.since_version is not None and request.since_version>case.case_version:
            raise ValueError("since_version is newer than the case")
        diff=compare_versions(self.repository.get_case(case.case_id,request.since_version),case) if request.since_version else None
        inventory=self.assets.list_case_inventory(case.case_id)
        if inventory["case_version"]!=case.case_version: raise VersionConflict(case.case_version,inventory["case_version"])
        linked={(e.file_id,e.file_version) for e in case.evidence}
        files=[f.model_dump(mode="json") for f in inventory["files"]]
        unlinked=[f for f in files if (f["file_id"],f["file_version"]) not in linked]
        views=[];version_diffs={}
        for output in inventory["outputs"]:
            deadline(request)
            stale=output.case_version!=case.case_version
            reasons=[];related={"fact_ids":[],"evidence_ids":[],"claim_ids":[]}
            if stale:
                if output.case_version not in version_diffs:
                    version_diffs[output.case_version]=compare_versions(self.repository.get_case(case.case_id,output.case_version),case)
                change=version_diffs[output.case_version]
                fids={fid for link in output.links for fid in link.fact_ids}
                eids={eid for link in output.links for eid in link.evidence_ids}
                cids={c.get("claim_id") for c in (output.structured_data or {}).get("claims",[]) if isinstance(c,dict)} if isinstance((output.structured_data or {}).get("claims",[]),list) else set()
                related=dict(fact_ids=sorted(fids&changed_ids(change,"facts")),evidence_ids=sorted(eids&changed_ids(change,"evidence")),claim_ids=sorted(cids&changed_ids(change,"claims")))
                if any(related.values()): reasons.append("输出直接关联的事实、材料或请求已变化")
                if change.substantive_changed: reasons.append("案件依据已变化；即使未直接关联，也可能有新的不利材料或前提")
                if any(change.changes["statements"].values()): reasons.append("新增或变化的用户陈述尚需解释，不能自动认定原结论仍适用")
                if not reasons: reasons.append("案件版本已改变，尚未证明本输出可沿用")
            if unlinked: reasons.append("存在尚未关联到案件证据的上传文件，需先解析并确认")
            views.append(dict(output_id=output.output_id,output_version=output.output_version,output_type=output.output_type,
                case_version=output.case_version,review_status=output.review_status,stale=stale,needs_review=bool(reasons),
                reasons=reasons,changed_dependencies=related,artifact_id=output.artifact_id,
                action="review_then_regenerate" if reasons else "retain_existing_status"))
        suggested=[]
        if unlinked: suggested.extend(["parse_documents","update_case"])
        if diff and any(diff.changes["statements"].values()): suggested.append("CaseIntakeService.prepare_review")
        if any(o["needs_review"] for o in views): suggested.extend(["check_evidence","verify_output"])
        return ContinuationData(case_snapshot=case,changes_since=diff,files=files,unlinked_files=unlinked,outputs=views,
            suggested_interfaces=suggested,inventory_truncated=inventory["truncated"],limitations=[
                "这里只恢复案件业务数据；Agent运行检查点、预算及新run_id由成员4管理。",
                "输出按各自原案件版本比较，保守提示复核；不自动改变旧输出状态或生成新文书。",
                "最多返回500个输入文件版本和500份最新成果，截断时不得视为完整清单；全文另按ID读取。",
                "应用须在调用前验证用户的案件权限。"])
