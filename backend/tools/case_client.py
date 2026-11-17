"""Shared synchronous tool entry point for Runtime and evaluation callers."""

from __future__ import annotations

from datetime import datetime, timezone
from time import monotonic
from typing import Callable
import warnings

from pydantic import ValidationError

from backend.case.contracts import InvocationContext
from backend.case.operations import REQUEST_MODELS, RESULT_MODELS
from ..case.models import BusinessError, BusinessResult, ContractModel
from backend.case.errors import CaseNotFound, VersionConflict
from .case_contracts import ARGUMENT_MODELS, OPERATION_STAGES, ToolInvocation


class CaseToolClient:
    """Shared validation and service dispatch for model and fixed-sequence calls.

    The application supplies a trusted InvocationContext and expected version.
    Only arguments may come from a model. The application's reply flow records
    user messages through RepositoryCasePort before applying confirmations.
    """

    def __init__(self, service, *, activity_sink=None):
        self.service = service
        self.activity_sink = activity_sink if activity_sink is not None else getattr(service,"activity",None)

    def invoke(self, name: str, invocation: ToolInvocation | dict) -> BusinessResult:
        result,receipt=self.invoke_recorded(name,invocation)
        if receipt.get("recording_failed"):
            warnings.warn("业务结果已返回，但调用结束记录未保存；不要据此自动重试业务操作。",RuntimeWarning,stacklevel=2)
        return result

    def invoke_recorded(self, name, invocation, *, run_id=None, step_id=None,
                        handler: Callable[[ContractModel], BusinessResult] | None = None):
        """Record one call; Agent adapters may bind a concrete service method.

        The handler and correlation IDs come from application code, never from
        model arguments. Named dispatch remains available to fixed/evaluation callers.
        """
        if self.activity_sink is None:
            return self._invoke(name,invocation,handler=handler),dict(recorded=False,reason="activity_store_not_configured")
        try:
            call=ToolInvocation.model_validate(invocation.model_dump(mode="json") if isinstance(invocation,ToolInvocation) else invocation)
            self.service.repository.get_case(call.context.case_id)
            if not isinstance(name,str) or not 0<len(name)<=200:
                raise ValueError("invalid tool name")
        except (ValueError,TypeError,KeyError):
            return self._invoke(name,invocation,handler=handler),dict(recorded=False,reason="invalid_or_unknown_case_context")
        try:
            start=self.activity_sink.begin(name,call,run_id=run_id,step_id=step_id)
        except Exception:
            return self._error(call.context,call.case_version,"tool_error","调用记录无法初始化，本次业务操作未执行。",{"operation_started":False}),dict(recorded=False,operation_started=False)
        started=monotonic()
        result=self._invoke(name,call,handler=handler)
        duration=round((monotonic()-started)*1000)
        try:
            sequence=self.activity_sink.finish(start,result,duration)
            return result,dict(recorded=True,attempt_id=start["attempt_id"],finished_sequence=sequence)
        except Exception:
            # The business write may already be committed. Never disguise it as a retryable tool failure.
            return result,dict(recorded=False,recording_failed=True,attempt_id=start["attempt_id"],operation_started=True)

    def _invoke(self, name: str, invocation: ToolInvocation | dict, *,
                handler: Callable[[ContractModel], BusinessResult] | None = None) -> BusinessResult:
        context = None
        version = None
        try:
            payload = invocation.model_dump(mode="json") if isinstance(invocation, ToolInvocation) else invocation
            if isinstance(payload, dict):
                context = InvocationContext.model_validate(payload.get("context"))
            call = ToolInvocation.model_validate(payload)
            context, version = call.context, call.case_version
            if name not in ARGUMENT_MODELS:
                return self._error(context, version, "not_found", "业务工具不存在。")
            if context.deadline_at and datetime.now(timezone.utc) >= context.deadline_at:
                return self._error(context, version, "timeout", "工具调用已过期。")
            arguments = ARGUMENT_MODELS[name].model_validate(call.arguments).model_dump(mode="json")
            snapshot = self.service.repository.get_case(context.case_id)
            if name != "update_case" and snapshot.case_version != version:
                raise VersionConflict(version, snapshot.case_version)
            allowed_stages = list(OPERATION_STAGES[name])
            if snapshot.stage not in allowed_stages:
                return self._error(context, snapshot.case_version, "invalid_request", "当前案件阶段不允许此工具。",
                                   {"reason": "stage_not_allowed", "allowed_stages": allowed_stages})
            if name == "update_case":
                if arguments["expected_case_version"] != version:
                    return self._error(context, version, "invalid_request", "更新的预期版本必须等于调用版本。")
                request_fields = {"context": context, **arguments}
            elif name == "verify_output":
                output = self.service.assets.get_output(context.case_id, arguments["output_id"])["output"]
                request_fields = {
                    "context": context,
                    "case_version": snapshot.case_version,
                    "case_snapshot": snapshot,
                    "candidate_output": output,
                    "cited_sources": output.source_refs,
                }
            else:
                request_fields = {"context": context, "case_version": snapshot.case_version, **arguments}
                if "case_snapshot" in REQUEST_MODELS[name].model_fields:
                    request_fields["case_snapshot"] = snapshot
            request = REQUEST_MODELS[name].model_validate(request_fields)
            execute = handler if handler is not None else getattr(self.service, name)
            result = execute(request)
            return RESULT_MODELS[name].model_validate(result.model_dump(mode="json"))
        except VersionConflict as exc:
            return self._error(context, exc.actual, "version_conflict", "调用的案件版本已过期。",
                               {"expected_case_version": exc.expected, "actual_case_version": exc.actual})
        except CaseNotFound:
            return self._error(context, version, "not_found", "案件、文件或业务输出不存在。")
        except ValidationError as exc:
            issues = [{"location": list(item["loc"]), "type": item["type"], "message": item["msg"]}
                      for item in exc.errors(include_url=False)]
            return self._error(context, version, "invalid_request", "业务参数不合法。", {"issues": issues})
        except (ValueError, TypeError):
            return self._error(context, version, "invalid_request", "业务参数或来源适配不合法。")
        except Exception as exc:
            return self._error(context, version, "tool_error", "业务工具执行失败。",
                               {"exception_type": type(exc).__name__})

    def evaluate(self, name: str, invocation: ToolInvocation | dict) -> dict:
        """Member 5 envelope. Valid context is required for request correlation."""
        from evaluation.adapters.case_results import to_evaluation_result
        raw_context = invocation.context if isinstance(invocation, ToolInvocation) else invocation.get("context")
        context = InvocationContext.model_validate(raw_context)
        started = monotonic()
        result = self.invoke(name, invocation)
        return to_evaluation_result(result, context, duration_ms=round((monotonic() - started) * 1000))

    @staticmethod
    def _error(context, version, code, message, details=None):
        return BusinessResult(
            case_id=context.case_id if context else None,
            case_version=version,
            status="error",
            summary=message,
            data=None,
            error=BusinessError(code=code, message=message, details=details),
        )
