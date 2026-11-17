"""Validate case structure and internal references."""

from pydantic import ValidationError

from ..models import BusinessError, BusinessResult, CaseSnapshot


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
