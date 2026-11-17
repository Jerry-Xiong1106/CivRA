"""CivRA hooks around create_agent; LangGraph owns history and sequential dispatch."""
from __future__ import annotations

import json
import time
from dataclasses import replace
from datetime import datetime, timedelta, timezone

from langchain.agents.middleware import AgentMiddleware, hook_config
from langchain_core.messages import AIMessage, ToolMessage

from backend.agent.model.llm import latest_answer, message_dict, normalize_reply, response_from_message
from backend.agent.model.prompts import assemble
from backend.agent.runtime.builtins import BUILTINS
from backend.agent.runtime.state import Checkpoint
from backend.common.schemas import TERMINAL_STATUSES, SourceRef, ToolCall, ToolContext, ToolError, ToolResult, Wait
from backend.agent.runtime.execution import ToolExecutor, validate_call
from backend.tools.policy import policy_of
from backend.agent.routing import tool_allowed


def allowed(name, searches, rounds, config):
    return ((name != "search_statutes" or config.max_search_calls is None or searches < config.max_search_calls)
            and (name != "ask_user" or config.max_clarification_rounds is None
                 or rounds < config.max_clarification_rounds))


def limits(searches, rounds, config, route_selected=False):
    """Expose runtime data; model behavior belongs to model/prompts.py."""
    return {
        "search_calls": searches,
        "max_search_calls": config.max_search_calls,
        "clarification_rounds": rounds,
        "max_clarification_rounds": config.max_clarification_rounds,
        "max_questions_per_round": config.max_questions_per_round,
        "route_selected_this_run": route_selected,
    }


class RunMiddleware(AgentMiddleware):
    def __init__(self, cp: Checkpoint, llm, tools, cases, store):
        self.cp, self.llm, self.cases, self.store = cp, llm, cases, store
        self.business_tools = {t.name: t for t in tools}
        self.executor = ToolExecutor()
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
        tools = [t for t in self.business_tools.values() if cp.stage in policy_of(t).stages
                 and (cfg.enabled_tools is None or t.name in cfg.enabled_tools)] + [
            t for t in BUILTINS.values() if cp.stage in policy_of(t).stages]
        tools = [t for t in tools if tool_allowed(cp.conversation_route, t.name)
                 and allowed(t.name, cp.search_calls, cp.clarification_rounds, cfg)]
        names = {spec.name for spec in tools}
        messages = assemble(
            request.messages, snapshot, cfg.max_context_chars, route=cp.conversation_route,
            runtime_context=limits(cp.search_calls, cp.clarification_rounds, cfg, cp.route_selected),
        )
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
        if reason := self.cp.budget_reason(time.monotonic() - self.started_at, before_tool=True):
            self.stop("budget_exhausted", reason)
            return self._message(call["id"], call["name"], ToolResult.fail("NOT_ALLOWED", reason))
        call = ToolCall(id=call["id"], tool_name=call["name"], arguments=call["args"])
        snapshot = self.cases.get_snapshot(self.cp.case_id)
        self.cp.case_version, self.cp.stage = snapshot.case_version, snapshot.stage
        result = (self._control(call, request, handler) if call.tool_name in BUILTINS
                  else self._business_tool(call, request, handler))
        return self._message(call.id, call.tool_name, result)

    def _business_tool(self, call: ToolCall, request, handler) -> ToolResult:
        cp = self.cp
        native_tool = self.business_tools.get(call.tool_name)
        policy = policy_of(native_tool) if native_tool is not None else None
        loop, limit = policy.feedback_loop if policy else None, cp.config.max_feedback_rounds
        with self.store.step(cp, "tool_call", call.tool_name, call.arguments, self.model_step) as step:
            ctx = ToolContext(case_id=cp.case_id, run_id=cp.run_id, step_id=step.step_id,
                              case_version=cp.case_version, stage=cp.stage,
                              deadline=datetime.now(timezone.utc) + timedelta(seconds=policy.timeout_s if policy else 30))
            refs, attempts = [], 0
            if not tool_allowed(cp.conversation_route, call.tool_name):
                result = ToolResult.fail("NOT_ALLOWED", "咨询入口不执行案件办理操作；请回答当前问题，或由用户明确发起办理请求。")
            elif loop and cp.feedback_rounds.get(loop, 0) >= limit:
                result = ToolResult.fail("NOT_ALLOWED", f"{loop} 已达到 {limit} 轮上限，请停止重试，在 finish 的 open_issues 中说明缺口。")
            elif not allowed(call.tool_name, cp.search_calls, cp.clarification_rounds, cp.config):
                result = ToolResult.fail("NOT_ALLOWED", "已达对话检索次数上限；请交付已有结果并说明未知项，不继续检索。")
            else:
                if call.tool_name == "search_statutes":
                    cp.search_calls += 1
                # Per-call immutable snapshot: no shared mutable runtime state for
                # timed-out workers. ToolNode injects this runtime into @tool.
                tool_request = replace(request, runtime=replace(request.runtime, context=ctx))

                def execute_native():
                    message = handler(tool_request)
                    return ToolResult.model_validate_json(message.content)

                execution = self.executor.execute(native_tool, call.tool_name, call.arguments, ctx,
                                                   execute_native, cp.config.enabled_tools)
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

    def _control(self, call: ToolCall, request, handler) -> ToolResult:
        cp = self.cp
        result = validate_call(BUILTINS[call.tool_name], call.arguments, cp.stage)
        if result is None and not allowed(call.tool_name, cp.search_calls, cp.clarification_rounds, cp.config):
            result = ToolResult.fail("NOT_ALLOWED", "已达事实追问轮数上限；请根据已有信息回答并列出未知项，仍可提供路线选择。")
        if result is None and call.tool_name == "ask_user":
            questions = call.arguments.get("questions", [])
            repeated = any(q["text"].strip() in cp.asked_questions for q in questions)
            if len(questions) > cp.config.max_questions_per_round or repeated:
                result = ToolResult.fail("INVALID_ARGS", f"每轮最多{cp.config.max_questions_per_round}个关键问题；已问过的问题不得原样重复，未知信息可保留后交付建议。")
        if result is None:
            # Native control tools return action arguments; state changes stay here.
            try:
                call.arguments = json.loads(handler(request).content)
            except Exception as exc:
                result = ToolResult.fail("INVALID_ARGS", f"Control tool failed ({type(exc).__name__})")
        if result is None and call.tool_name == "finish":
            self.finish(call.arguments, self.model_step)
            return ToolResult(status="ok", summary="运行结束。")
        kind = "error" if result is not None else "ask_user" if call.tool_name == "ask_user" else "route_choice"
        with self.store.step(cp, kind, call.tool_name, call.arguments, self.model_step) as step:
            if result is None:
                wait_kind = "clarification" if call.tool_name == "ask_user" else "route_selection"
                cp.wait = Wait(wait_id=f"wait_{cp.run_id}_{step.step_id}", **{"kind": wait_kind, **call.arguments})
                if call.tool_name == "ask_user":
                    cp.clarification_rounds += 1
                    cp.asked_questions.extend(q.text.strip() for q in cp.wait.questions)
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
