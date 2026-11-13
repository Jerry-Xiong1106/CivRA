"""Thin Member 4 Runtime binding for the shared Member 3 tool client."""

from __future__ import annotations

from pydantic import ValidationError
import warnings

from backend.case.adapters import runtime_source_match, to_runtime_tool_result
from backend.case.client import CaseToolClient, ToolInvocation, get_case_tool_contracts
from backend.case.contracts import InvocationContext
from backend.case.repository import CaseNotFound


def register_case_tools(registry, service, *, source_resolver=None):
    from backend.common.schemas import ToolResult, ToolSpec

    client = CaseToolClient(service)
    resolver = source_resolver or service.get_source
    contracts = get_case_tool_contracts()
    duplicates = set(registry.names()) & set(contracts)
    if duplicates:
        raise ValueError("case tools already registered: " + ", ".join(sorted(duplicates)))

    def invoke(name, arguments, context):
        result = None
        try:
            invocation = ToolInvocation(
                context=InvocationContext(
                    request_id=f"{context.run_id}:tool:{context.step_id}:{name}",
                    case_id=context.case_id,
                    experiment_run_id=None,
                    deadline_at=context.deadline,
                ),
                case_version=context.case_version,
                arguments=arguments,
            )
            result, receipt = client.invoke_recorded(name, invocation, run_id=context.run_id, step_id=str(context.step_id))
            if receipt.get("recording_failed"):
                warnings.warn("业务调用已完成，结束记录失败；请按run_id/step_id查询，勿自动重试写操作。", RuntimeWarning)
            converted = to_runtime_tool_result(result, source_resolver=resolver, result_type=ToolResult)
            material_refs = []
            if result.data is not None and name != "update_case":
                report_fields = ("missing_facts", "conflicts", "duplicates", "material_gaps", "supplementary_checklist")
                material_refs = [ref for field in report_fields for finding in getattr(result.data, field, [])
                                 for ref in finding.material_refs]
                material_refs += [ref for entry in getattr(result.data, "entries", []) for ref in entry.material_refs]
                material_refs += [segment.material_ref for segment in getattr(result.data, "text_segments", [])]
            matches = (converted.data or {}).get("matches", [])
            seen = {match["source_ref"]["source_id"] for match in matches}
            snapshot = service.repository.get_case(context.case_id) if material_refs else None
            for ref in material_refs:
                source_id = (f"material:{ref.file_id}:{ref.file_version}:{ref.extraction_version}:"
                             f"{ref.page}:{ref.paragraph}:{ref.span_start}:{ref.span_end}")
                if source_id in seen:
                    continue
                seen.add(source_id)
                locator = {"chunk_id": "text"}
                if ref.page is not None:
                    locator["page_start"] = ref.page
                if ref.paragraph:
                    locator["paragraph"] = ref.paragraph
                if ref.span_start is not None:
                    locator["chunk_id"] += f":chars[{ref.span_start}:{ref.span_end}]"
                if ref.extraction_version:
                    locator["chunk_id"] += ":extraction:" + ref.extraction_version
                title = next((item.file_name for item in snapshot.evidence
                              if (item.file_id, item.file_version) == (ref.file_id, ref.file_version)), None)
                if title is None:
                    title = service.assets.get_file(context.case_id, ref.file_id, ref.file_version)[0].file_name
                matches.append(runtime_source_match({
                    "source_id": source_id, "source_type": "user_material", "title": title,
                    "locator": locator, "version": ref.file_version, "text": ref.quote or "",
                }))
            if material_refs:
                if converted.data is None:
                    converted.data = {}
                converted.data["matches"] = matches
            return converted
        except CaseNotFound:
            failure = ToolResult.fail("NOT_FOUND", "案件、文件或业务输出不存在。")
            if result is not None:
                failure.case_version = result.case_version
                failure.error.details = {"output_refs": result.output_refs}
            return failure
        except (ValidationError, ValueError):
            failure = ToolResult.fail("INVALID_ARGS", "业务参数或来源适配不合法。")
            if result is not None:
                failure.case_version = result.case_version
                failure.error.details = {"output_refs": result.output_refs}
            return failure
        except Exception as exc:
            return ToolResult(
                status="error", data=None, summary="业务结果适配失败，可按输出引用查询已保存结果。",
                case_version=result.case_version if result else None,
                error={
                    "code": "INTERNAL", "message": "业务结果适配失败。", "retryable": False,
                    "details": {"exception_type": type(exc).__name__,
                                "output_refs": result.output_refs if result else []},
                },
            )

    for name, contract in contracts.items():
        registry.register(ToolSpec(**contract), lambda args, ctx, name=name: invoke(name, args, ctx))
    return registry
