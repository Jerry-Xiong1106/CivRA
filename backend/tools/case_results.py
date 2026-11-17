"""Convert case results to native tool responses and pinned source matches."""

from collections.abc import Callable, Mapping
from typing import Any

from backend.case.models import BusinessResult, SourcePointer


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
    matches = []
    for pointer in result.source_refs:
        source = dict(source_resolver(pointer))
        required = {"source_id", "source_type", "title", "locator", "version"}
        if not required <= source.keys():
            raise ValueError("resolver must return the pinned business source fields")
        if source["source_id"] != pointer.source_id or source["version"] != pointer.source_version:
            raise ValueError("resolved source identity/version differs from the pointer")
        matches.append(runtime_source_match(source))
    error = None
    if result.error is not None:
        error = {
            "code": RUNTIME_ERRORS[result.error.code],
            "message": result.error.message,
            "retryable": result.error.retryable,
            "details": {**(result.error.details or {}), "business_code": result.error.code},
        }
    data = None if result.data is None else result.model_dump(mode="json")
    if data is not None:
        data["matches"] = matches
    elif matches:
        data = {"matches": matches}
    payload = {
        "status": result.status,
        "data": data,
        "summary": result.summary,
        "error": error,
        "case_version": result.case_version,
    }
    return result_type.model_validate(payload) if result_type is not None else payload


def runtime_source_match(source: Mapping[str, Any]) -> dict:
    """Keep original text beside the current Runtime's structured source metadata."""
    from backend.common.schemas import SourceRef

    kind = source["source_type"]
    locator = source["locator"]
    if isinstance(locator, str):
        field = "article" if kind in {"law", "regulation", "statute", "judicial_interpretation"} else "paragraph"
        locator = {field: locator}
    else:
        locator = dict(locator)
        page = locator.pop("page", None)
        if page is not None:
            locator.setdefault("page_start", page)
    for key in ("page_start", "page_end"):
        if source.get(key) is not None:
            locator.setdefault(key, source[key])
    ref = {
        "source_id": source["source_id"],
        "source_type": "statute" if kind in {"law", "regulation"} else kind,
        "title": source["title"],
        "locator": locator,
        "source_version": source.get("source_version", source["version"]),
    }
    for key in ("url", "effective_from", "effective_to", "decision_date", "retrieved_at",
                "retrieved_date", "source_file", "content_sha256"):
        if source.get(key) is not None:
            ref[key] = source[key]
    if "content_sha256" not in ref and source.get("text_sha256"):
        ref["content_sha256"] = source["text_sha256"]
    return {"text": source.get("text", source.get("excerpt", "")),
            "source_ref": SourceRef.model_validate(ref).model_dump(mode="json")}
