"""CivRA hooks around create_agent; LangGraph owns history and sequential dispatch.

The middleware enforces what the model must not decide on its own: budgets and
cancellation, which tools exist at the case's stage, tool execution limits, and
the three control actions (ask the user, ask the user to confirm a route step,
finish). What to do within a step is left to the model and the prompt.
"""
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
from backend.agent.runtime.execution import ToolExecutor, validate_call
from backend.agent.runtime.state import Checkpoint
from backend.common.schemas import TERMINAL_STATUSES, SourceRef, ToolCall, ToolContext, ToolResult, Wait
from backend.tools.policy import policy_of


def remaining_budget(cp: Checkpoint) -> dict:
    cfg, used = cp.config, cp.used_since_reply()
    remaining = {name: max(0, maximum - used[name]) for name, maximum in (
        ("model_calls", cfg.max_model_calls), ("tool_calls", cfg.max_tool_calls), ("tokens", cfg.max_total_tokens),
    ) if maximum is not None}
    if (remaining.get("model_calls", 2) <= 1 or remaining.get("tool_calls", 3) <= 2
            or cfg.max_total_tokens and remaining["tokens"] <= cfg.max_total_tokens // 5):
        remaining["note"] = "接近本轮预算，请优先交付已有结果及关键缺口。"
    return remaining


def limits(cp: Checkpoint) -> dict:
    cfg = cp.config
    return {"search_calls": cp.search_calls, "max_search_calls": cfg.max_search_calls,
            "clarification_rounds": cp.clarification_rounds,
            "max_clarification_rounds": cfg.max_clarification_rounds,
            "max_questions_per_round": cfg.max_questions_per_round,
            "remaining_budget": remaining_budget(cp)}


class RunMiddleware(AgentMiddleware):
    def __init__(self, cp: Checkpoint, llm, tools, cases, store):
        self.cp, self.llm, self.cases, self.store = cp, llm, cases, store
        self.business_tools = {t.name: t for t in tools}
        self.executor = ToolExecutor()
        self.started_at = time.monotonic()
        self.model_step = 0
        self.last_answer = ""
        self.reply_text = ""   # text of the latest model reply, shown with a wait it opens
        self.stop_reason = None

    # ------------------------------------------------------------ run control

    def stop(self, status, reason):
        self.cp.stop(status, reason, self.last_answer)
        self.stop_reason = reason

    def _cancelled_or_ended(self):
        if self.cp.status not in TERMINAL_STATUSES and self.store.is_cancel_requested(self.cp.run_id):
            self.stop("cancelled", "用户取消")
        return self.cp.status in TERMINAL_STATUSES

    def _refresh(self):
        snapshot = self.cases.get_snapshot(self.cp.case_id)
        self.cp.case_version, self.cp.stage = snapshot.case_version, snapshot.stage
        return snapshot

    @hook_config(can_jump_to=["end"])
    def before_model(self, state, runtime):
        self.last_answer = latest_answer(state["messages"])
        if not self._cancelled_or_ended() and self.cp.wait is None:
            if reason := self.cp.budget_reason(time.monotonic() - self.started_at):
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

    # ------------------------------------------------------------ tool availability

    def _available(self, tool) -> bool:
        cp, cfg = self.cp, self.cp.config
        name = tool.name
        if cp.stage not in policy_of(tool).stages:
            return False
        if name in BUILTINS:
            return (name != "ask_user" or cfg.max_clarification_rounds is None
                    or cp.clarification_rounds < cfg.max_clarification_rounds)
        if cfg.enabled_tools is not None and name not in cfg.enabled_tools:
            return False
        if cp.tool_failures.get(name, 0) >= cfg.max_tool_failures:
            return False
        return name != "search_statutes" or cfg.max_search_calls is None or cp.search_calls < cfg.max_search_calls

    def wrap_model_call(self, request, handler):
        cp, cfg = self.cp, self.cp.config
        snapshot = self._refresh()
        names = {t.name for t in [*self.business_tools.values(), *BUILTINS.values()] if self._available(t)}
        messages = assemble(request.messages, snapshot, cfg.max_context_chars, runtime_context=limits(cp))
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
                self.reply_text = reply.text or ""
                result = response_from_message(reply, self.llm, int((time.monotonic() - tick) * 1000))
            except Exception as exc:
                step.status = "error"
                step.error = ToolResult.fail("UPSTREAM", f"模型调用失败（{type(exc).__name__}）").error
                step.result_summary = step.error.message
                cp.usage.tokens_complete, cp.usage.estimated_cost = False, None
            else:
                self.store.add_model_usage(cp, result)
                step.result_ref = self.store.save_result(cp.run_id, step.step_id, {
                    "request": {**params, "tools": sorted(names),
                                "messages": [message_dict(message) for message in messages]},
                    "response": result.model_dump(),
                })
                step.result_summary = "模型选择：" + (", ".join(c.tool_name for c in result.tool_calls) or "回答")
                step.token_usage, step.cost, step.decision_note = result.usage, result.cost, result.content or None
        if step.error:
            self.stop("failed", step.error.message)
            return AIMessage(content="")
        return response

    # ------------------------------------------------------------ tool execution

    def wrap_tool_call(self, request, handler):
        # The runner sets max_concurrency=1, so tool calls run one at a time.
        call = request.tool_call
        if self._cancelled_or_ended() or self.cp.wait is not None:
            return self._message(call["id"], call["name"], ToolResult(status="error", summary="未执行：运行已暂停或结束。"))
        if reason := self.cp.budget_reason(time.monotonic() - self.started_at, before_tool=True):
            self.stop("budget_exhausted", reason)
            return self._message(call["id"], call["name"], ToolResult.fail("NOT_ALLOWED", reason))
        call = ToolCall(id=call["id"], tool_name=call["name"], arguments=call["args"])
        self._refresh()
        if call.tool_name in BUILTINS:
            result = self._control(call, request, handler)
        else:
            result = self._business_tool(call, request, handler)
        return self._message(call.id, call.tool_name, result)

    def _business_tool(self, call: ToolCall, request, handler) -> ToolResult:
        cp = self.cp
        tool = self.business_tools.get(call.tool_name)
        policy = policy_of(tool) if tool is not None else None
        with self.store.step(cp, "tool_call", call.tool_name, call.arguments, self.model_step) as step:
            refs, attempts = [], 0
            if tool is not None and not self._available(tool):
                result = ToolResult.fail("NOT_ALLOWED", f"{call.tool_name} 当前不可用（阶段不符、已达次数上限或连续失败），"
                                                        "请用已有信息继续。")
            else:
                ctx = ToolContext(case_id=cp.case_id, run_id=cp.run_id, step_id=step.step_id,
                                  case_version=cp.case_version, stage=cp.stage,
                                  deadline=datetime.now(timezone.utc) + timedelta(seconds=policy.timeout_s if policy else 30))
                # Per-call context: a timed-out worker never shares mutable runtime state.
                tool_request = replace(request, runtime=replace(request.runtime, context=ctx))

                def execute_native():
                    return ToolResult.model_validate_json(handler(tool_request).content)

                execution = self.executor.execute(tool, call.tool_name, call.arguments, ctx,
                                                  execute_native, cp.config.enabled_tools)
                result, attempts = execution.result, execution.attempts
                if call.tool_name == "search_statutes" and attempts and result.status != "error":
                    cp.search_calls += 1
                self._count_failure(call.tool_name, result)
                try:
                    refs = [SourceRef.model_validate(match["source_ref"])
                            for match in (result.data or {}).get("matches", [])]
                except (KeyError, TypeError, ValueError):
                    result = ToolResult.fail("INTERNAL", "工具返回的来源引用不符合约定。")
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

    def _count_failure(self, name, result):
        cp = self.cp
        if result.status != "error":
            cp.tool_failures.pop(name, None)
            return
        cp.tool_failures[name] = cp.tool_failures.get(name, 0) + 1
        if cp.tool_failures[name] == cp.config.max_tool_failures:
            note = f"{name} 连续 {cp.config.max_tool_failures} 次未成功，本轮不再使用，相关内容需另行核实。"
            cp.limit_notes.append(note)
            if result.error is not None:
                result.error.details = {**(result.error.details or {}), "next_action": note}

    # ------------------------------------------------------------ control actions

    def _control(self, call: ToolCall, request, handler) -> ToolResult:
        cp = self.cp
        result = validate_call(BUILTINS[call.tool_name], call.arguments, cp.stage)
        if result is None and not self._available(BUILTINS[call.tool_name]):
            result = ToolResult.fail("NOT_ALLOWED", "已达追问轮数上限；请根据已有信息回答并列出未知项。")
        if result is None and call.tool_name == "ask_user":
            questions = call.arguments["questions"]
            texts = [q["text"].strip() for q in questions]
            if (len(questions) > cp.config.max_questions_per_round or len(set(texts)) != len(texts)
                    or len({q["question_id"] for q in questions}) != len(questions)
                    or any(text in cp.asked_questions for text in texts)):
                result = ToolResult.fail("INVALID_ARGS", f"每轮最多{cp.config.max_questions_per_round}个不重复的问题，"
                                                         "已问过的问题不要原样再问；未知信息可保留后先交付。")
        if result is None and call.tool_name == "confirm_step":
            progress = self.cases.route_progress(cp.case_id)
            current = progress["current_step"] if progress else None
            if call.arguments["step_id"] != current:
                result = ToolResult.fail("INVALID_ARGS", f"只能确认当前步骤（{current or '无'}）。")
        if result is None:
            try:
                call.arguments = json.loads(handler(request).content)
            except Exception as exc:
                result = ToolResult.fail("INVALID_ARGS", f"控制动作失败（{type(exc).__name__}）")
        if result is not None:
            with self.store.step(cp, "error", call.tool_name, call.arguments, self.model_step) as step:
                step.status, step.error, step.result_summary = result.status, result.error, result.summary
            return result
        if call.tool_name == "finish":
            self.finish(call.arguments, self.model_step)
            return ToolResult(status="ok", summary="运行结束。")
        self._start_wait(call.tool_name, call.arguments, self.model_step)
        return ToolResult(status="ok", summary="已发送给用户，等待回复。")

    def _start_wait(self, tool_name, args, parent):
        cp = self.cp
        if tool_name == "ask_user":
            step_type = "ask_user"
            wait = dict(kind=args.get("kind", "clarification"), questions=args["questions"],
                        message=self.reply_text or None)
        else:
            step_type = "step_confirmation"
            # The step's deliverable is the answer the user reads while deciding.
            wait = dict(kind="step_confirmation", step_id=args["step_id"], summary=args["summary"],
                        message=args["answer"])
            self.last_answer = args["answer"]
        with self.store.step(cp, step_type, tool_name, args, parent) as step:
            cp.wait = Wait(wait_id=f"wait_{cp.run_id}_{step.step_id}", **wait)
            if tool_name == "ask_user":
                cp.clarification_rounds += 1
                cp.asked_questions.extend(q.text.strip() for q in cp.wait.questions)
                step.result_summary = "；".join(q.text for q in cp.wait.questions)
            else:
                step.result_summary = f"请用户确认步骤 {args['step_id']}：{args['summary']}"
            step.status = "waiting"
        cp.status = "waiting_user"
        self.store.emit(cp, "run.waiting_user", wait=cp.wait.model_dump())

    def finish(self, args: dict, parent=None):
        with self.store.step(self.cp, "finish", "finish", parent=parent) as step:
            self.cp.complete(args, self.cases.get_snapshot(self.cp.case_id).case_version)
            step.result_summary = self.cp.final.answer[:200]

    @staticmethod
    def _message(call_id, name, result):
        return ToolMessage(content=result.model_dump_json(exclude_none=True), tool_call_id=call_id,
                           name=name, status="error" if result.status == "error" else "success")
