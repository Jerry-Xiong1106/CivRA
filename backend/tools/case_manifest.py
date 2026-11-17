"""Export business schemas and native tool metadata for integration callers."""

from backend.case.operations import REQUEST_MODELS, RESULT_MODELS
from backend.case.models import IMPLEMENTATION_VERSION, SCHEMA_VERSION, BusinessOutput, BusinessResult, CaseSnapshot, Money


def get_contract_manifest() -> dict:
    """Schemas are generated from live models; no parallel hand-maintained JSON schema."""
    from backend.tools.case_contracts import ToolInvocation
    from .case_tools import get_case_tool_contracts
    from backend.case.presentation.statistics import CaseStatisticsRequest, CaseStatisticsData
    from backend.case.review.corrections import CorrectionRequest, ReviewComparisonRequest, CorrectionData
    from backend.case.intake.history import HistoryRequest, HistoryData, VersionDiffRequest, VersionDiffData, ContinuationRequest, ContinuationData
    from backend.case.storage.activity import ActivityRequest, ActivityData
    from backend.case.review.answer_review import AnswerReviewRequest
    from backend.case.review.contracts import VerificationData
    auxiliary={
        "build_case_statistics":(CaseStatisticsRequest,CaseStatisticsData),
        "prepare_output_correction":(CorrectionRequest,CorrectionData),
        "compare_verification_reports":(ReviewComparisonRequest,CorrectionData),
        "get_case_history":(HistoryRequest,HistoryData),
        "compare_case_versions":(VersionDiffRequest,VersionDiffData),
        "prepare_case_continuation":(ContinuationRequest,ContinuationData),
        "get_tool_activity":(ActivityRequest,ActivityData),
        "review_answer":(AnswerReviewRequest,VerificationData),
    }
    return {
        "schema_version": SCHEMA_VERSION,
        "implementation_version": IMPLEMENTATION_VERSION,
        "implemented": ["validate_case_snapshot", "load_development_case", "get_contract_manifest",
                        "runtime_case_view", "to_runtime_tool_result", "to_evaluation_result",
                        *REQUEST_MODELS],
        "business_operations": {name:"implemented" for name in REQUEST_MODELS},
        "tool_operations": get_case_tool_contracts(),
        "auxiliary_operations":{name:{"entrypoint":"CaseService."+name,"registered_as_agent_tool":False} for name in auxiliary},
        "schemas": {
            "case_snapshot": CaseSnapshot.model_json_schema(),
            "money": Money.model_json_schema(),
            "business_output": BusinessOutput.model_json_schema(),
            "case_validation_result": BusinessResult[CaseSnapshot].model_json_schema(),
            "tool_invocation": ToolInvocation.model_json_schema(),
            **{name + "_request": model.model_json_schema() for name, model in REQUEST_MODELS.items()},
            **{name + "_result": model.model_json_schema() for name, model in RESULT_MODELS.items()},
            **{name + "_request": pair[0].model_json_schema() for name,pair in auxiliary.items()},
            **{name + "_result": BusinessResult[pair[1]].model_json_schema() for name,pair in auxiliary.items()},
        },
    }
