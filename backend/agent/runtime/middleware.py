"""CivRA hooks around create_agent; LangGraph owns history and sequential dispatch."""
from __future__ import annotations

import time
from dataclasses import replace
from datetime import datetime, timedelta, timezone

from langchain.agents.middleware import AgentMiddleware, hook_config
from langchain.tools import ToolRuntime
from langchain_core.messages import AIMessage, ToolMessage
from langchain_core.tools import StructuredTool

from backend.agent.model.llm import latest_answer, message_dict, normalize_reply, response_from_message
from backend.agent.model.prompts import assemble
from backend.agent.runtime.builtins import BUILTINS
from backend.agent.runtime.state import Checkpoint
from backend.common.schemas import TERMINAL_STATUSES, SourceRef, ToolCall, ToolContext, ToolError, ToolResult, Wait
from backend.tools.registry import validate_call


class RunMiddleware(AgentMiddleware):
    def __init__(self, cp: Checkpoint, llm, registry, cases, store):
        self.cp, self.llm, self.registry, self.cases, self.store = cp, llm, registry, cases, store
        self.started_at = time.monotonic()
        self.model_step = 0
        self.last_answer = ""
        self.stop_reason = None

    def stop(self, status, reason):
        self.cp.stop(status, reason, self.last_answer)
        self.stop_reason = reason
        return self.cp.info()

    def _cancelled_or_ended(self):
        if self.cp.status not in TERMINAL_STATUSES and self.store.is_cancel_requested(self.cp.run_id):
            self.stop("cancelled", "用户取消")
        return self.cp.status in TERMINAL_STATUSES

    @hook_config(can_jump_to=["end"])
    def before_model(self, state, runtime):
        self.last_answer = latest_answer(state["messages"])
        if not self._cancelled_or_ended():
            if self.cp.wait is not None:
                self.cp.status = "waiting_user"
                self.store.emit(self.cp, "run.waiting_user", wait=self.cp.wait.model_dump())
            elif reason := self.cp.budget_reason(time.monotonic() - self.started_at):
                self.stop("budget_exhausted", reason)
        return {"jump_to": "end"} if self.cp.status != "running" else None

    @hook_config(can_jump_to=["end"])
    def after_model(self, state, runtime):
        self.last_answer = latest_answer(state["messages"])
        if self._cancelled_or_ended():
            return {"jump_to": "end"}
        reply = state["messages"][-1]
        if not reply.tool_calls:
            self.finish({"answer": reply.text or ""})
            return {"jump_to": "end"}
        return None

    def wrap_model_call(self, request, handler):
        cp, cfg = self.cp, self.cp.config
        snapshot = self.cases.get_snapshot(cp.case_id)
        cp.case_version, cp.stage = snapshot.case_version, snapshot.stage
        tools = self.registry.available(cp.stage, cfg.enabled_tools) + [
            spec for spec in BUILTINS.values() if cp.stage in spec.stages]
        names = {spec.name for spec in tools}
        messages = assemble(request.messages, snapshot, cfg.max_context_chars)
        params = {"model": cfg.model, "temperature": cfg.temperature,
                  "max_tokens": cfg.max_output_tokens, "seed": cfg.seed}
        with self.store.step(cp, "model_call", arguments=params) as step:
            self.model_step = step.step_id
            cp.usage.model_calls += 1
            tick = time.monotonic()
            try:
                response = handler(request.override(
                    messages=messages, system_message=None,
                    tools=[tool for tool in request.tools if tool.name in names],
                    model_settings={key: value for key, value in params.items() if value is not None}))
                reply = normalize_reply(response.result[0]).model_copy(update={"id": f"{cp.run_id}:{step.step_id}"})
                response = replace(response, result=[reply])
                result = response_from_message(reply, self.llm, int((time.monotonic() - tick) * 1000))
            except Exception as exc:
                step.status = "error"
                step.error = ToolError(code="UPSTREAM", message=f"模型调用失败（{type(exc).__name__}）")
                step.result_summary = step.error.message
                cp.usage.tokens_complete, cp.usage.estimated_cost = False, None
            else:
                self.store.add_model_usage(cp, result)
                step.result_ref = self.store.save_result(cp.run_id, step.step_id, {
                    "request": {**params, "tools": [tool.name for tool in tools],
                                "messages": [message_dict(message) for message in messages]},
                    "response": result.model_dump(),
                })
                summary = ", ".join(call.tool_name for call in result.tool_calls) or "回答"
                step.result_summary = f"模型选择：{summary}"
                step.token_usage, step.cost, step.decision_note = result.usage, result.cost, result.content or None
        if step.error:
            self.stop("failed", step.error.message)
            return AIMessage(content="")
        return response

    def wrap_tool_call(self, request, handler):
        # Runner sets max_concurrency=1 for synchronous ToolNode execution.
        call = request.tool_call
        if self._cancelled_or_ended() or self.cp.wait is not None:
            return self._message(call["id"], call["name"], ToolResult(status="error", summary="未执行：运行已暂停或结束。"))
        if request.tool is None:
            return self.execute(ToolCall(id=call["id"], tool_name=call["name"], arguments=call["args"]))
        return handler(request)

    def execute(self, call: ToolCall) -> ToolMessage:
        snapshot = self.cases.get_snapshot(self.cp.case_id)
        self.cp.case_version, self.cp.stage = snapshot.case_version, snapshot.stage
        result = self._control(call) if call.tool_name in BUILTINS else self._business_tool(call)
        return self._message(call.id, call.tool_name, result)

    def _business_tool(self, call: ToolCall) -> ToolResult:
        cp = self.cp
        spec = self.registry.get_spec(call.tool_name)
        loop, limit = spec.feedback_loop if spec else None, cp.config.max_feedback_rounds
        with self.store.step(cp, "tool_call", call.tool_name, call.arguments, self.model_step) as step:
            ctx = ToolContext(case_id=cp.case_id, run_id=cp.run_id, step_id=step.step_id,
                              case_version=cp.case_version, stage=cp.stage,
                              deadline=datetime.now(timezone.utc) + timedelta(seconds=spec.timeout_s if spec else 30))
            refs, attempts = [], 0
            if loop and cp.feedback_rounds.get(loop, 0) >= limit:
                result = ToolResult.fail("NOT_ALLOWED", f"{loop} 已达到 {limit} 轮上限，请停止重试，在 finish 的 open_issues 中说明缺口。")
            else:
                execution = self.registry.execute(call.tool_name, call.arguments, ctx, cp.config.enabled_tools)
                result, attempts = execution.result, execution.attempts
                try:
                    refs = [SourceRef.model_validate(match["source_ref"])
                            for match in (result.data or {}).get("matches", [])]
                except (KeyError, TypeError, ValueError):
                    result = ToolResult.fail("INTERNAL", "工具返回的来源引用不符合约定。")
                if loop and result.status != "ok":
                    cp.feedback_rounds[loop] = cp.feedback_rounds.get(loop, 0) + 1
                    if cp.feedback_rounds[loop] == limit:
                        cp.limit_notes.append(f"检索与核验已重复 {limit} 轮仍未得到充分依据（{loop}），相关结论需进一步核实。")
            cp.usage.tool_calls += 1
            cp.usage.tool_attempts += attempts
            if result.case_version is not None:
                cp.case_version = result.case_version
            sources = {ref.source_id: ref for ref in cp.source_refs}
            for ref in refs:
                sources.setdefault(ref.source_id, ref)
            cp.source_refs = list(sources.values())
            step.source_refs, step.attempt = refs, max(attempts, 1)
            step.status, step.error, step.result_summary = result.status, result.error, result.summary
            step.result_ref = self.store.save_result(cp.run_id, step.step_id, {
                "tool_name": call.tool_name, "arguments": call.arguments, "attempts": attempts,
                "result": result.model_dump(),
            })
        return result

    def _control(self, call: ToolCall) -> ToolResult:
        cp = self.cp
        result = validate_call(BUILTINS[call.tool_name], call.arguments, cp.stage)
        if result is None and call.tool_name == "finish":
            self.finish(call.arguments, self.model_step)
            return ToolResult(status="ok", summary="运行结束。")
        kind = "error" if result is not None else "ask_user" if call.tool_name == "ask_user" else "route_choice"
        with self.store.step(cp, kind, call.tool_name, call.arguments, self.model_step) as step:
            if result is None:
                wait_kind = "clarification" if call.tool_name == "ask_user" else "route_selection"
                cp.wait = Wait(wait_id=f"wait_{cp.run_id}_{step.step_id}", **{"kind": wait_kind, **call.arguments})
                step.status = "waiting"
                step.result_summary = "；".join(question.text for question in cp.wait.questions) or "请用户选择处理路线"
                result = ToolResult(status="ok", summary="已发送给用户，等待回复。")
            else:
                step.status, step.error, step.result_summary = "error", result.error, result.summary
        return result

    def finish(self, args: dict, parent=None):
        with self.store.step(self.cp, "finish", "finish", parent=parent) as step:
            self.cp.complete(args, self.cases.get_snapshot(self.cp.case_id).case_version)
            step.result_summary = self.cp.final.answer[:200]
        return self.cp.info()

    @staticmethod
    def _message(call_id, name, result):
        return ToolMessage(content=result.model_dump_json(exclude_none=True), tool_call_id=call_id,
                           name=name, status="error" if result.status == "error" else "success")

    def framework_tool(self, spec):
        def run(runtime: ToolRuntime, **arguments):
            return self.execute(ToolCall(id=runtime.tool_call_id, tool_name=spec.name, arguments=arguments))
        return StructuredTool.from_function(func=run, name=spec.name, description=spec.description,
                                            args_schema=spec.parameters)
