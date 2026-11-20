"""Business invocation envelope for evaluation callers."""

from backend.case.contracts import InvocationContext
from backend.case.models import IMPLEMENTATION_VERSION, BusinessResult


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


def evaluate_case_tool(client, name, invocation) -> dict:
    """Run one case tool through CaseToolClient and wrap it in member 5's envelope.

    A valid context is required for request correlation.
    """
    from time import monotonic
    from backend.tools.case_contracts import ToolInvocation
    raw_context = invocation.context if isinstance(invocation, ToolInvocation) else invocation.get("context")
    context = InvocationContext.model_validate(raw_context)
    started = monotonic()
    result = client.invoke(name, invocation)
    return to_evaluation_result(result, context, duration_ms=round((monotonic() - started) * 1000))
