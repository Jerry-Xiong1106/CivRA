"""Prepare correction handoffs from persisted verification reports; no Agent loop."""
from pydantic import Field
from ..business_support import deadline, digest, failure
from ..contracts import CaseRequest
from .contracts import VerificationData
from ..models import BusinessResult, ContractModel, Identifier, PositiveInt
from ..errors import VersionConflict


class CorrectionRequest(CaseRequest):
    verification_output_id: Identifier
    verification_output_version: PositiveInt = 1


class ReviewComparisonRequest(CorrectionRequest):
    previous_verification_output_id: Identifier
    previous_verification_output_version: PositiveInt = 1


class CorrectionData(ContractModel):
    target_output_id: str
    target_output_version: int
    verification_output_id: str
    tasks: list[dict]
    checks_not_performed: list[str]
    disposition: str
    previous_review: dict | None = None
    recommended_max_rounds: int = 3
    automatically_executed: bool = False
    limitations: list[str] = Field(default_factory=list)


class CorrectionService:
    def __init__(self,repository,assets):
        self.repository,self.assets=repository,assets

    def _report(self,case_id,output_id,version):
        stored=self.assets.get_output(case_id,output_id,version)["output"]
        if stored.output_type!="verification":
            raise ValueError("expected a persisted verification output")
        data=stored.structured_data or {}
        if "_business_result" not in data:
            raise ValueError("verification report is missing its persisted business envelope")
        report=VerificationData.model_validate(data["_business_result"]["data"])
        target=self.assets.get_output(case_id,report.target_output_id,report.target_output_version)["output"]
        if report.target_digest!=digest(target):
            raise ValueError("verification report does not match the saved target content")
        return stored,report

    def prepare(self,request,*,compare=False):
        case_id,version=None,None
        try:
            model=ReviewComparisonRequest if compare else CorrectionRequest
            request=model.model_validate(request.model_dump(mode="json") if hasattr(request,"model_dump") else request)
            case_id,version=request.context.case_id,request.case_version
            deadline(request)
            current=self.repository.get_case(case_id)
            if current.case_version!=version: raise VersionConflict(version,current.case_version)
            stored,report=self._report(case_id,request.verification_output_id,request.verification_output_version)
            if stored.case_version!=version:
                raise ValueError("verification is stale; verify against the current case before preparing corrections")
            previous=None
            if compare:
                old,old_report=self._report(case_id,request.previous_verification_output_id,request.previous_verification_output_version)
                if old.case_version>version: raise ValueError("previous review is newer than the current review")
                old_ids={f.finding_id for f in old_report.findings}; new_ids={f.finding_id for f in report.findings}
                previous=dict(verification_output_id=old.output_id,target_output_id=old_report.target_output_id,
                    still_reported=sorted(old_ids&new_ids),no_longer_reported=sorted(old_ids-new_ids),newly_reported=sorted(new_ids-old_ids),
                    same_target=old_report.target_digest==report.target_digest,
                    scope_reduced=bool(set(old_report.checks_performed)-set(report.checks_performed)),
                    semantic_resolution_proven=False)
            deadline(request)
            head=self.repository.get_case(case_id)
            if head.case_version!=version: raise VersionConflict(version,head.case_version)
            data=CorrectionData(target_output_id=report.target_output_id,target_output_version=report.target_output_version,
                verification_output_id=stored.output_id,tasks=report.correction_tasks,checks_not_performed=report.checks_not_performed,
                disposition=report.disposition,previous_review=previous,limitations=[
                    "仅准备修正任务并比较报告；不会修改事实、原稿或自动启动检索。",
                    "成员4按预算与最多三轮的项目约定组织执行；轮数与停止条件由Runtime实际记录。",
                    "问题未再次报告不等于已解决，须比较检查范围并完成语义及人工核验。"])
            return BusinessResult[CorrectionData](case_id=case_id,case_version=version,status="insufficient",data=data,
                summary="已准备有保存依据的修正清单；旧输出保留，后续重新生成并核验。",output_refs=[stored.output_id,report.target_output_id])
        except (ValueError,TypeError,KeyError,TimeoutError) as exc:
            return failure(case_id,version,exc)
