"""Contract inspection and validation entry points shared by the business phases."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from pydantic import ValidationError

from .contracts import REQUEST_MODELS, RESULT_MODELS
from .models import (
    IMPLEMENTATION_VERSION, SCHEMA_VERSION, BusinessError, BusinessOutput,
    BusinessResult, CaseSnapshot, Money,
)

DEVELOPMENT_CASE = Path(__file__).resolve().parents[2] / "data/examples/member3/traffic_accident_v1/case_snapshot.json"


def validate_case_snapshot(payload: dict) -> BusinessResult[CaseSnapshot]:
    """Validate structure and reference integrity; does not establish factual/legal truth."""
    try:
        snapshot = CaseSnapshot.model_validate(payload)
    except ValidationError as exc:
        # Return locations and explanations, not a second copy of sensitive input.
        issues = [{"location": list(item["loc"]), "message": item["msg"], "type": item["type"]}
                  for item in exc.errors(include_url=False)]
        case_id = payload.get("case_id") if isinstance(payload, dict) else None
        version = payload.get("case_version") if isinstance(payload, dict) else None
        return BusinessResult[CaseSnapshot](
            case_id=case_id if isinstance(case_id, str) and case_id.strip() else None,
            case_version=version if type(version) is int and version >= 1 else None,
            status="error", summary="案件结构或引用关系不合法。", data=None,
            error=BusinessError(code="invalid_request", message="case snapshot validation failed", details={"issues": issues}),
        )
    return BusinessResult[CaseSnapshot](
        case_id=snapshot.case_id, case_version=snapshot.case_version,
        status="ok", summary="案件结构及内部引用关系通过校验；尚未执行证据或法律核验。", data=snapshot,
    )


def load_development_case() -> CaseSnapshot:
    """Load public synthetic fixture, checking material hashes and span/quote alignment."""
    snapshot = CaseSnapshot.model_validate_json(DEVELOPMENT_CASE.read_text(encoding="utf-8"))
    if not snapshot.synthetic:
        raise ValueError("the development fixture must be explicitly synthetic")
    directory = DEVELOPMENT_CASE.parent / "materials"
    for evidence in snapshot.evidence:
        path = (directory / evidence.file_name).resolve()
        if not path.is_relative_to(directory.resolve()):
            raise ValueError("fixture material path escapes its directory")
        content = path.read_bytes()
        if hashlib.sha256(content).hexdigest() != evidence.content_sha256:
            raise ValueError(f"fixture checksum differs: {evidence.file_id}")
        text = content.decode("utf-8")
        refs = list(evidence.material_refs)
        refs += [ref for fact in snapshot.facts for ref in fact.material_refs if ref.file_id == evidence.file_id]
        for ref in refs:
            if ref.span_start is not None:
                if ref.span_end > len(text) or (ref.quote is not None and text[ref.span_start:ref.span_end] != ref.quote):
                    raise ValueError(f"fixture span/quote mismatch: {evidence.file_id}")
    return snapshot


def get_contract_manifest() -> dict:
    """Schemas are generated from live models; no parallel hand-maintained JSON schema."""
    from .client import ToolInvocation, get_case_tool_contracts
    from .statistics import CaseStatisticsRequest, CaseStatisticsData
    from .corrections import CorrectionRequest, ReviewComparisonRequest, CorrectionData
    from .history import HistoryRequest, HistoryData, VersionDiffRequest, VersionDiffData, ContinuationRequest, ContinuationData
    from .activity import ActivityRequest, ActivityData
    from .answer_review import AnswerReviewRequest
    from .contracts import VerificationData
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
