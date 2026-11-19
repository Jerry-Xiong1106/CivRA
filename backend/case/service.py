"""Callable case business service with injected storage, OCR and semantic review."""

from .contracts import IdempotentCaseRepository
from .operations import REQUEST_MODELS
from pydantic import ValidationError
from .materials.evidence import build_evidence_directory, check_evidence
from .materials.evidence_rules import EvidenceRules, load_evidence_rules
from .materials.evidence_similarity import read_material_text
from .calculation.fee_rules import LOCAL_SOURCE_PREFIX
from .research.sources import CaseResearchService
from .intake.updates import update_case
from .storage.assets import SQLiteAssetStore
from .calculation.calculations import CostCalculator
from .materials.documents import DocumentParser
from .drafting.documents import DocumentDrafter
from .models import BusinessError, BusinessResult
from .materials.ocr import default_ocr_engine
from .storage.repository import SQLiteCaseRepository
from .storage.outputs import OutputPersistence
from .planning.routes import compare_routes
from .review.verification import OutputVerifier
from .presentation.visualization import build_visualization
from .drafting.templates import load_builtin_templates
from .planning.workflow import CaseWorkflowService
from .planning.guide import RouteGuide
from .business_support import failure
from .research.outcomes import OutcomeAnalyzer, OutcomeAnalysisRequest, OutcomeAnalysisData
from .review.corrections import CorrectionService
from .intake.history import CaseHistoryService
from .intake.summary import CaseIntakeService
from .presentation.statistics import CaseStatistics, CaseStatisticsRequest, CaseStatisticsData
from .storage.activity import SQLiteActivityStore
from .drafting.official_forms import OfficialFormService


class CaseService:
    """Inject Member 1's repository, or the local SQLite reference implementation."""

    def __init__(self, repository: IdempotentCaseRepository, *, assets=None, ocr=None, templates=None,
                 source_resolver=None, semantic_gateway=None, drafting_gateway=None, evidence_rules=None, activity_store=None,
                 official_template_library=None, form_wording_gateway=None):
        self.repository = repository
        self.external_source_resolver=source_resolver
        if assets is None and not isinstance(repository, SQLiteCaseRepository):
            raise ValueError("non-SQLite repositories must supply an asset store")
        self.assets = assets if assets is not None else SQLiteAssetStore(repository)
        self.outputs = OutputPersistence(repository, self.assets)
        self.evidence_rules=load_evidence_rules() if evidence_rules is None else EvidenceRules.model_validate(evidence_rules)
        self.parser=DocumentParser(self.assets,ocr=ocr if ocr is not None else default_ocr_engine())
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
        self.intake=CaseIntakeService(repository)
        self.statistics=CaseStatistics(repository,self.get_source)
        self.activity=activity_store if activity_store is not None else SQLiteActivityStore(repository) if isinstance(repository, SQLiteCaseRepository) else None
        self.routes=RouteGuide(self)

    def check_evidence(self, request):
        return check_evidence(request,rules=self.evidence_rules,text_reader=self._read_evidence_text)

    def build_evidence_directory(self, request):
        return self.outputs.save("build_evidence_directory",request,
            build_evidence_directory(request,rules=self.evidence_rules,text_reader=self._read_evidence_text),"evidence_directory")

    def _read_evidence_text(self, case_id, evidence):
        return read_material_text(self.assets,case_id,evidence)

    def update_case(self, request):
        return update_case(request, self.repository, assets=self.assets)

    def summarize_case(self, request):
        return self.intake.summarize(request)

    def prepare_fact_review(self, request):
        return self.intake.prepare_review(request)

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
        return self.outputs.run("compare_routes",request,
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
        return self.outputs.run("get_route_workflow",request,self.workflow.build,"workflow")

    def get_workflow_progress(self, request):
        return self.workflow.get_progress(request)

    def record_workflow_progress(self, request):
        return self.workflow.record_progress(request)

    def analyze_outcomes(self, request):
        return self.outputs.run("analyze_outcomes",request,self.outcomes.build,"analysis",
            request_model=OutcomeAnalysisRequest,result_model=BusinessResult[OutcomeAnalysisData])

    def draft_document(self, request):
        result=self.drafter.draft_document(request)
        if result.data is not None:
            result.output_refs=[result.data.output_id]
        return result

    def verify_output(self, request):
        return self.outputs.run("verify_output",request,self.verifier.verify_output,"verification")

    def review_answer(self, request):
        """Read-only answer/citation review; caller retains raw answers and this report."""
        from .review.answer_review import review_answer
        return review_answer(self, request)

    def build_visualization(self, request):
        return self.outputs.run("build_visualization",request,
            lambda payload:build_visualization(payload,evidence_checker=self.check_evidence),"visualization")

    def build_case_statistics(self,request):
        return self.outputs.run("build_case_statistics",request,self.statistics.build,"visualization",
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
