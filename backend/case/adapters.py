"""Explicit bridges for the Runtime v0.5 and Evaluation v0.1 draft contracts."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any

from .contracts import InvocationContext
from .models import IMPLEMENTATION_VERSION, BusinessResult, CaseSnapshot, SourcePointer


def runtime_case_view(snapshot: CaseSnapshot) -> dict:
    """Construct Member 4's current CaseSnapshot shape without importing Agent code."""
    data = snapshot.model_dump(mode="json")
    return {
        "case_id": snapshot.case_id,
        "case_version": snapshot.case_version,
        "stage": snapshot.stage,
        "summary": {key: value for key, value in data.items() if key not in {"case_id", "case_version", "stage"}},
    }


def to_evaluation_result(
    result: BusinessResult,
    context: InvocationContext,
    *,
    duration_ms: int,
    attempt_count: int = 1,
) -> dict:
    """Outer status describes invocation; inner status preserves business insufficiency."""
    if result.case_id is not None and result.case_id != context.case_id:
        raise ValueError("result belongs to another case")
    if type(duration_ms) is not int or duration_ms < 0:
        raise ValueError("duration_ms must be a nonnegative integer")
    if type(attempt_count) is not int or attempt_count < 1:
        raise ValueError("attempt_count must be a positive integer")
    failed = result.status == "error"
    error = result.error.model_dump(mode="json") if failed else None
    if failed:
        error["details"] = {**(error["details"] or {}), "case_id": result.case_id, "case_version": result.case_version}
    return {
        "schema_version": "0.1",
        "request_id": context.request_id,
        "status": "error" if failed else "ok",
        "data": None if failed else result.model_dump(mode="json"),
        "error": error,
        "meta": {
            "duration_ms": duration_ms,
            "implementation_version": IMPLEMENTATION_VERSION,
            "attempt_count": attempt_count,
        },
    }


RUNTIME_ERRORS = {
    "invalid_request": "INVALID_ARGS", "not_found": "NOT_FOUND",
    "version_conflict": "INVALID_ARGS", "timeout": "TIMEOUT",
    "rate_limited": "UPSTREAM", "provider_error": "UPSTREAM",
    "tool_error": "INTERNAL", "cancelled": "NOT_ALLOWED",
    "not_implemented": "NOT_ALLOWED",
}


def to_runtime_tool_result(
    result: BusinessResult,
    *,
    source_resolver: Callable[[SourcePointer], Mapping[str, Any]] | None = None,
    result_type: Any = None,
):
    """Pass result_type=backend.common.schemas.ToolResult to obtain the real model.

    SourceRef is owned by Member 2. The caller must resolve every foreign key to
    the same source version; this module never creates an authority or replaces it
    with a newer version. It does not install tools into the global registry.
    """
    if result.source_refs and source_resolver is None:
        raise ValueError("source_resolver is required for referenced sources")
    resolved = []
    for pointer in result.source_refs:
        source = dict(source_resolver(pointer))
        required = {"source_id", "source_type", "title", "locator", "version"}
        if not required <= source.keys():
            raise ValueError("resolver must return the Runtime v0.5 SourceRef fields")
        if source["source_id"] != pointer.source_id or source["version"] != pointer.source_version:
            raise ValueError("resolved source identity/version differs from the pointer")
        # Full text is needed by semantic review, but Runtime citations use the
        # compact SourceRef shape. Normalize the legal aliases to its vocabulary.
        if source["source_type"] in {"law", "regulation"}:
            source["source_type"] = "statute"
        allowed = required | {"excerpt", "url"}
        resolved.append({key: value for key, value in source.items() if key in allowed})
    error = None
    if result.error is not None:
        error = {
            "code": RUNTIME_ERRORS[result.error.code],
            "message": result.error.message,
            "retryable": result.error.retryable,
            "details": {**(result.error.details or {}), "business_code": result.error.code},
        }
    payload = {
        "status": result.status,
        "data": None if result.data is None else result.model_dump(mode="json"),
        "summary": result.summary,
        "source_refs": resolved,
        "error": error,
        "case_version": result.case_version,
    }
    return result_type.model_validate(payload) if result_type is not None else payload
