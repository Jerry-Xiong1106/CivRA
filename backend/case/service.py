"""Callable case business service with injected storage, OCR and semantic review."""

import hashlib
import json
from datetime import datetime, timezone

from .contracts import IdempotentCaseRepository, InvocationContext, REQUEST_MODELS, RESULT_MODELS
from pydantic import ValidationError
from .evidence import build_evidence_directory, check_evidence
from .evidence_rules import EvidenceRules, load_evidence_rules
from .evidence_similarity import read_material_text
from .fee_rules import LOCAL_SOURCE_PREFIX
from .research import CaseResearchService
from .updates import update_case
from .assets import CaseAssetStore
from .calculations import CostCalculator
from .documents import DocumentParser
from .drafting import DocumentDrafter, TemplateRegistry
from .models import BusinessError, BusinessOutput, BusinessResult
from .ocr import OcrEngineRegistry
from .repository import CaseNotFound, VersionConflict
from .routes import compare_routes
from .verification import OutputVerifier
from .visualization import build_visualization
from .templates import load_builtin_templates
from .workflow import CaseWorkflowService
from .business_support import failure
from .outcomes import OutcomeAnalyzer, OutcomeAnalysisRequest, OutcomeAnalysisData
from .corrections import CorrectionService
from .history import CaseHistoryService
from .statistics import CaseStatistics, CaseStatisticsRequest, CaseStatisticsData
from .activity import CaseActivityStore
from .official_forms import OfficialFormService


class CaseService:
    """Inject Member 1's repository, or the local SQLite reference implementation."""

    def __init__(self, repository: IdempotentCaseRepository, *, assets=None, ocr=None, templates=None,
                 source_resolver=None, semantic_gateway=None, drafting_gateway=None, evidence_rules=None, activity_store=None,
                 official_template_library=None, form_wording_gateway=None):
        self.repository = repository
        self.external_source_resolver=source_resolver
        self.assets=assets or CaseAssetStore(repository)
        self.evidence_rules=load_evidence_rules() if evidence_rules is None else EvidenceRules.model_validate(evidence_rules)
        if ocr is None:
            ocr=OcrEngineRegistry().create("paddleocr", {"ocr_version":"PP-OCRv6","device":"cpu",
                "detection_model_name":"PP-OCRv6_small_det","recognition_model_name":"PP-OCRv6_small_rec","cpu_threads":4})
        self.parser=DocumentParser(self.assets,ocr=ocr)
        self.calculator=CostCalculator(repository,assets=self.assets)
        self.research=CaseResearchService(repository,source_resolver=self.get_source)
        self.workflow=CaseWorkflowService(repository,self.assets,evidence_checker=self.check_evidence,
                                          source_reviewer=self.research.review_sources)
        self.outcomes=OutcomeAnalyzer(evidence_checker=self.check_evidence,source_reviewer=self.research.review_sources)
        self.drafter=DocumentDrafter(self.assets,templates or load_builtin_templates(),source_resolver=self.get_source,
            drafting_gateway=drafting_gateway,evidence_checker=self.check_evidence)
        self.official_forms=OfficialFormService(repository,self.assets,library=official_template_library,
            gateway=form_wording_gateway,evidence_checker=self.check_evidence)
        self.verifier=OutputVerifier(source_resolver=self.get_source,semantic_gateway=semantic_gateway,assets=self.assets,
            calculator=self.calculator,evidence_checker=self.check_evidence)
        self.corrections=CorrectionService(repository,self.assets)
        self.history=CaseHistoryService(repository,self.assets)
        self.statistics=CaseStatistics(repository,self.get_source)
        self.activity=activity_store if activity_store is not None else CaseActivityStore(repository) if hasattr(repository,"_connection") else None

    def check_evidence(self, request):
        return check_evidence(request,rules=self.evidence_rules,text_reader=self._read_evidence_text)

    def build_evidence_directory(self, request):
        return self._persist("build_evidence_directory",request,
            build_evidence_directory(request,rules=self.evidence_rules,text_reader=self._read_evidence_text),"evidence_directory")

    def _read_evidence_text(self, case_id, evidence):
        return read_material_text(self.assets,case_id,evidence)

    def update_case(self, request):
        case_id,version=None,None
        try:
            model=REQUEST_MODELS["update_case"]
            request=model.model_validate(request.model_dump(mode="json") if hasattr(request,"model_dump") else request)
            case_id,version=request.context.case_id,request.expected_case_version
            current=self.repository.get_case(case_id)
            existing={item.evidence_id for item in current.evidence}
            for evidence in request.changes.upsert_evidence:
                try:
                    record,_=self.assets.get_file(case_id,evidence.file_id,evidence.file_version)
                except CaseNotFound:
                    # Phase-two cases may predate the shared file store. Their
                    # identities remain immutable; new evidence must be uploaded.
                    if evidence.evidence_id in existing:
                        continue
                    raise ValueError("new evidence must reference an uploaded file in this case")
                for field in ("file_name","media_type","document_type","content_sha256"):
                    if getattr(record,field)!=getattr(evidence,field):
                        raise ValueError("evidence metadata differs from the uploaded file")
                for ref in evidence.material_refs:
                    if ref.extraction_version:
                        self.assets.read_extraction(case_id,ref)
            for fact in request.changes.upsert_facts:
                for ref in fact.material_refs:
                    if ref.extraction_version:
                        self.assets.read_extraction(case_id,ref)
            return update_case(request, self.repository)
        except CaseNotFound:
            return BusinessResult(case_id=case_id,case_version=version,status="error",data=None,
                summary="案件、文件或提取版本不存在。",error=BusinessError(code="not_found",message="case or material not found"))
        except (ValidationError,ValueError) as exc:
            return BusinessResult(case_id=case_id,case_version=version,status="error",data=None,
                summary="变更与已上传材料不匹配。",error=BusinessError(code="invalid_request",message=str(exc).splitlines()[0]))

    def parse_documents(self, request):
        return self.parser.parse_documents(request)

    def calculate_costs(self, request):
        return self.calculator.calculate_costs(request)

    def summarize_material_costs(self, request):
        return self.calculator.summarize_materials(request)

    def prepare_research(self, request):
        return self.research.prepare_queries(request)

    def review_research_sources(self, request):
        return self.research.review_sources(request)

    def get_source(self, pointer):
        """Compose the explicitly namespaced local fee source with Member 2's resolver."""
        from .models import SourcePointer
        pointer=SourcePointer.model_validate(pointer)
        if pointer.source_id.startswith(LOCAL_SOURCE_PREFIX):
            return self.calculator.fee_rule.resolve(pointer)
        if self.external_source_resolver is None:
            raise KeyError("external source resolver is not configured")
        return self.external_source_resolver(pointer)

    def compare_routes(self, request):
        return self._snapshot_call("compare_routes",request,
            lambda payload: compare_routes(payload, evidence_checker=self.check_evidence,
                                           source_reviewer=self.research.review_sources), "route_comparison")

    def get_route_workflow(self, request):
        case_id,version=None,None
        try:
            model=REQUEST_MODELS["get_route_workflow"]
            request=model.model_validate(request.model_dump(mode="json") if hasattr(request,"model_dump") else request)
            case_id,version=request.context.case_id,request.case_version
            request=self.workflow.resolve_version(request)
        except (ValidationError,ValueError,KeyError,TimeoutError) as exc:
            return failure(case_id,version,exc)
        return self._snapshot_call("get_route_workflow",request,self.workflow.build,"workflow")

    def get_workflow_progress(self, request):
        return self.workflow.get_progress(request)

    def record_workflow_progress(self, request):
        return self.workflow.record_progress(request)

    def analyze_outcomes(self, request):
        return self._snapshot_call("analyze_outcomes",request,self.outcomes.build,"analysis",
            request_model=OutcomeAnalysisRequest,result_model=BusinessResult[OutcomeAnalysisData])

    def draft_document(self, request):
        result=self.drafter.draft_document(request)
        if result.data is not None:
            result.output_refs=[result.data.output_id]
        return result

    def verify_output(self, request):
        return self._snapshot_call("verify_output",request,self.verifier.verify_output,"verification")

    def review_answer(self, request):
        """Read-only answer/citation review; caller retains raw answers and this report."""
        from .answer_review import review_answer
        return review_answer(self, request)

    def build_visualization(self, request):
        return self._snapshot_call("build_visualization",request,
            lambda payload:build_visualization(payload,evidence_checker=self.check_evidence),"visualization")

    def build_case_statistics(self,request):
        return self._snapshot_call("build_case_statistics",request,self.statistics.build,"visualization",
            request_model=CaseStatisticsRequest,result_model=BusinessResult[CaseStatisticsData])

    def prepare_output_correction(self,request):
        return self.corrections.prepare(request)

    def compare_verification_reports(self,request):
        return self.corrections.prepare(request,compare=True)

    def get_case_history(self,request):
        return self.history.history(request)

    def compare_case_versions(self,request):
        return self.history.diff(request)

    def prepare_case_continuation(self,request):
        return self.history.continuation(request)

    def get_tool_activity(self,request):
        if self.activity is None:
            return BusinessResult(status="error",summary="业务调用记录存储未配置。",data=None,
                error=BusinessError(code="not_implemented",message="activity store is not configured"))
        return self.activity.query(request)

    def _snapshot_call(self,name,request,operation,output_type,*,request_model=None,result_model=None):
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
            output_id=self._output_id(name,case_id,request.context.request_id)
            try:
                old=self.assets.get_output(case_id,output_id)["output"]
                if old.structured_data.get("_request_digest")!=self._digest(request):
                    raise ValueError("request_id was reused with different operation arguments")
                return (result_model or RESULT_MODELS[name]).model_validate(old.structured_data["_business_result"])
            except CaseNotFound:
                pass
            return self._persist(name,request,operation(request),output_type,request_model=model)
        except VersionConflict as exc:
            return BusinessResult(case_id=case_id,case_version=exc.actual,status="error",data=None,
                summary="案件版本已更新。",error=BusinessError(code="version_conflict",message=str(exc)))
        except CaseNotFound:
            return BusinessResult(case_id=case_id,case_version=version,status="error",data=None,
                summary="案件不存在。",error=BusinessError(code="not_found",message="案件不存在。"))
        except (ValidationError,ValueError) as exc:
            return BusinessResult(case_id=case_id,case_version=version,status="error",data=None,
                summary="案件快照或请求参数不合法。",error=BusinessError(code="invalid_request",message=str(exc).splitlines()[0]))

    def _persist(self, name, request, result, output_type, *, request_model=None):
        if result.status=="error":
            return result
        model=request_model or REQUEST_MODELS[name]
        request=model.model_validate(request.model_dump(mode="json") if hasattr(request,"model_dump") else request)
        context=request.context if hasattr(request,"context") else request["context"]
        context=InvocationContext.model_validate(context.model_dump(mode="json") if hasattr(context,"model_dump") else context)
        output_id=self._output_id(name,result.case_id,context.request_id)
        result.output_refs=[output_id]
        structured=result.data.model_dump(mode="json")
        structured.update(_request_digest=self._digest(request),_business_result=result.model_dump(mode="json"))
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
    def _output_id(name,case_id,request_id):
        return name+"_"+hashlib.sha256((case_id+":"+request_id).encode()).hexdigest()[:24]

    @staticmethod
    def _digest(request):
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
