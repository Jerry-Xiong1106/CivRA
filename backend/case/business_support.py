"""Small shared checks for the workflow and outcome business interfaces."""

from datetime import datetime, timezone
import hashlib
import json

from .models import BusinessError, BusinessResult
from .errors import CaseNotFound, VersionConflict


def deadline(request):
    if request.context.deadline_at and datetime.now(timezone.utc) >= request.context.deadline_at:
        raise TimeoutError("business operation deadline expired")


def digest(value):
    if hasattr(value, "model_dump"):
        value = value.model_dump(mode="json")
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode()).hexdigest()


def unique(values):
    result = []
    for value in values:
        if value not in result:
            result.append(value)
    return result


def case_basis(snapshot):
    raw = snapshot.model_dump(mode="json")
    # Adding a reply alone does not undo work. Substantive case changes require review.
    for field in ("case_version", "statements", "created_at", "updated_at"):
        raw.pop(field, None)
    return digest(raw)


def failure(case_id, version, exc):
    code = "version_conflict" if isinstance(exc, VersionConflict) else "not_found" if isinstance(exc, CaseNotFound) else (
        "timeout" if isinstance(exc, TimeoutError) else "invalid_request")
    if isinstance(exc, VersionConflict):
        version = exc.actual
    return BusinessResult(case_id=case_id, case_version=version, status="error", data=None,
        summary="业务请求未完成。", error=BusinessError(code=code, message=str(exc).splitlines()[0]))
