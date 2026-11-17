"""Versioned output persistence and replay for snapshot-based business operations."""

import hashlib
import json
from datetime import datetime, timezone

from pydantic import ValidationError

from ..contracts import InvocationContext
from ..operations import REQUEST_MODELS, RESULT_MODELS
from ..models import BusinessError, BusinessOutput, BusinessResult
from ..errors import CaseNotFound, VersionConflict


class OutputPersistence:
    def __init__(self, repository, assets):
        self.repository, self.assets = repository, assets

    def run(self,name,request,operation,output_type,*,request_model=None,result_model=None):
        case_id,version=None,None
        try:
            model=request_model or REQUEST_MODELS[name]
            request=model.model_validate(request.model_dump(mode="json") if hasattr(request,"model_dump") else request)
            case_id,version=request.context.case_id,request.case_version
            current=self.repository.get_case(case_id)
            if current.case_version!=version:
                raise VersionConflict(version,current.case_version)
            if current!=request.case_snapshot:
                raise ValueError("snapshot content differs from the stored case version")
            if request.context.deadline_at and datetime.now(timezone.utc)>=request.context.deadline_at:
                return BusinessResult(case_id=case_id,case_version=version,status="error",data=None,summary="请求已过期。",
                    error=BusinessError(code="timeout",message="request deadline expired"))
            output_id=self.output_id(name,case_id,request.context.request_id)
            try:
                old=self.assets.get_output(case_id,output_id)["output"]
                if old.structured_data.get("_request_digest")!=self.request_digest(request):
                    raise ValueError("request_id was reused with different operation arguments")
                return (result_model or RESULT_MODELS[name]).model_validate(old.structured_data["_business_result"])
            except CaseNotFound:
                pass
            return self.save(name,request,operation(request),output_type,request_model=model)
        except VersionConflict as exc:
            return BusinessResult(case_id=case_id,case_version=exc.actual,status="error",data=None,
                summary="案件版本已更新。",error=BusinessError(code="version_conflict",message=str(exc)))
        except CaseNotFound:
            return BusinessResult(case_id=case_id,case_version=version,status="error",data=None,
                summary="案件不存在。",error=BusinessError(code="not_found",message="案件不存在。"))
        except (ValidationError,ValueError) as exc:
            return BusinessResult(case_id=case_id,case_version=version,status="error",data=None,
                summary="案件快照或请求参数不合法。",error=BusinessError(code="invalid_request",message=str(exc).splitlines()[0]))

    def save(self, name, request, result, output_type, *, request_model=None):
        if result.status=="error":
            return result
        model=request_model or REQUEST_MODELS[name]
        request=model.model_validate(request.model_dump(mode="json") if hasattr(request,"model_dump") else request)
        context=request.context if hasattr(request,"context") else request["context"]
        context=InvocationContext.model_validate(context.model_dump(mode="json") if hasattr(context,"model_dump") else context)
        output_id=self.output_id(name,result.case_id,context.request_id)
        result.output_refs=[output_id]
        structured=result.data.model_dump(mode="json")
        structured.update(_request_digest=self.request_digest(request),_business_result=result.model_dump(mode="json"))
        output=BusinessOutput(output_id=output_id,output_version=1,case_id=result.case_id,case_version=result.case_version,
            output_type=output_type,review_status="needs_review" if result.status=="insufficient" else "unreviewed",
            text=result.summary,structured_data=structured,source_refs=result.source_refs)
        if name=="analyze_outcomes":
            output.links=result.data.output_links
        try:
            self.assets.save_output(output,deadline_at=context.deadline_at)
            result.output_refs=[output_id]
        except VersionConflict as exc:
            return BusinessResult(case_id=result.case_id,case_version=exc.actual,status="error",data=None,
                summary="案件已更新，拒绝保存基于旧版本的输出。",error=BusinessError(code="version_conflict",message=str(exc)))
        except ValueError as exc:
            return BusinessResult(case_id=result.case_id,case_version=result.case_version,status="error",data=None,
                summary="输出请求 ID 已被用于不同内容。",error=BusinessError(code="invalid_request",message=str(exc)))
        except TimeoutError:
            return BusinessResult(case_id=result.case_id,case_version=result.case_version,status="error",data=None,
                summary="请求已过期，未保存新输出。",error=BusinessError(code="timeout",message="output deadline expired"))
        return result

    @staticmethod
    def output_id(name,case_id,request_id):
        return name+"_"+hashlib.sha256((case_id+":"+request_id).encode()).hexdigest()[:24]

    @staticmethod
    def request_digest(request):
        payload=request.model_dump(mode="json") if hasattr(request,"model_dump") else request
        if isinstance(request, REQUEST_MODELS["compare_routes"]):
            # Empty additive fields preserve historical comparison request identities.
            for field in ("source_bindings", "user_expenses"):
                if not payload.get(field):
                    payload.pop(field, None)
        if isinstance(request, REQUEST_MODELS["get_route_workflow"]):
            for field in ("progress_version", "source_notes", "dates"):
                if not payload.get(field):
                    payload.pop(field, None)
        # Normalize mixed model/dict callers through the request model at call sites.
        def serialize(value):
            return value.model_dump(mode="json") if hasattr(value,"model_dump") else value.isoformat()
        return hashlib.sha256(json.dumps(payload,sort_keys=True,ensure_ascii=False,default=serialize).encode()).hexdigest()
