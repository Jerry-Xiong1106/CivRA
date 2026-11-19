"""CivRA hooks around create_agent; LangGraph owns history and sequential dispatch."""
from __future__ import annotations

import json
import re
import time
from dataclasses import replace
from datetime import datetime, timedelta, timezone

from langchain.agents.middleware import AgentMiddleware, hook_config
from langchain_core.messages import AIMessage, RemoveMessage, ToolMessage
from pydantic import ValidationError

from backend.agent.model.llm import latest_answer, message_dict, normalize_reply, response_from_message
from backend.agent.model.prompts import assemble
from backend.agent.runtime.builtins import BUILTINS
from backend.agent.runtime.state import Checkpoint
from backend.common.schemas import TERMINAL_STATUSES, SourceRef, ToolCall, ToolContext, ToolError, ToolResult, Wait
from backend.agent.runtime.execution import ToolExecutor, validate_call
from backend.tools.policy import policy_of
from backend.agent.tasks import (
    MEDIATION_QUESTIONS, TaskIntent, assess, current, mediation_gaps, record_mediation_answer, select_task,
)
from backend.agent.routing import tool_allowed
from backend.agent.litigation import ENTRY_TEXT, EVENTS
from backend.case.calculation.contracts import CalculationData


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


def remaining_budget(cp):
    cfg, usage = cp.config, cp.usage
    remaining = {name: max(0, maximum - used) for name, maximum, used in (
        ("model_calls", cfg.max_model_calls, usage.model_calls),
        ("tool_calls", cfg.max_tool_calls, usage.tool_calls),
        ("tokens", cfg.max_total_tokens, usage.prompt_tokens + usage.completion_tokens),
    ) if maximum is not None}
    if (remaining.get("model_calls", 2) <= 1 or remaining.get("tool_calls", 3) <= 2
            or cfg.max_total_tokens and remaining["tokens"] <= cfg.max_total_tokens // 5):
        remaining["note"] = "接近本轮预算，请优先交付已有结果及关键缺口。"
    return remaining


class RunMiddleware(AgentMiddleware):
    def __init__(self, cp: Checkpoint, llm, tools, cases, store):
        self.tasks = []
        self.reply_evidence = []
        self.reply_files = []
        self.draft_attempts = {}
        self.calculation_results = {}
        self.allow_task_change = False
        self.messages = []
        self.cp, self.llm, self.cases, self.store = cp, llm, cases, store
        self.business_tools = {t.name: t for t in tools}
        self.executor = ToolExecutor()
        self.started_at = time.monotonic()
        self.model_step = 0
        self.last_answer = ""
        self.stop_reason = None

    def task_rows(self):
        return [t.model_dump(mode="json") for t in self.tasks]

    def after_agent(self, state, runtime):
        return {"tasks": self.task_rows()}

    def stop(self, status, reason):
        self.cp.stop(status, reason, self.last_answer)
        self.stop_reason = reason
        return self.cp.info()

    def _cancelled_or_ended(self):
        if self.cp.status not in TERMINAL_STATUSES and self.store.is_cancel_requested(self.cp.run_id):
            self.stop("cancelled", "用户取消")
        return self.cp.status in TERMINAL_STATUSES

    def _invalid_calls_exhausted(self, tool):
        limit = policy_of(tool).invalid_args_limit if tool is not None else None
        return limit is not None and self.cp.feedback_rounds.get(f"invalid_args:{tool.name}", 0) >= limit

    @hook_config(can_jump_to=["end", "tools"])
    def before_model(self, state, runtime):
        self.messages = state["messages"]
        self.last_answer = latest_answer(self.messages)
        if not self._cancelled_or_ended():
            if self.cp.wait is None and (reason := self.cp.budget_reason(time.monotonic() - self.started_at)):
                self.stop("budget_exhausted", reason)
            elif self.cp.wait is None and (read := self._first_route_material()):
                return {"messages": [read], "tasks": self.task_rows(), "jump_to": "tools"}
            elif self.cp.routing_source == "litigation_feedback" and self.cp.wait is None:
                progress = self.cases.get_snapshot(self.cp.case_id).summary.get("route_progress") or {}
                if any(s["step_id"] == "submit" and s.get("event") == "accepted" for s in progress.get("steps", [])):
                    answer = "已按你的确认记录法院受理反馈。"
                    self.finish({"answer": answer, "task_result": {"delivered_text": answer, "missing": []}})
            elif (self.cp.routing_source == "litigation_entry" and self.cp.wait is None
                  and not self.cp.intake_wait_id and not self.cp.clarification_rounds
                  and self.cp.config.max_clarification_rounds != 0):
                snapshot = self.cases.get_snapshot(self.cp.case_id).summary
                if (not any(snapshot.get(k) for k in ("facts", "claims", "evidence", "recent_uploaded_files", "user_goals"))
                        and all(s["text"] == ENTRY_TEXT for s in snapshot.get("statements", []))
                        and snapshot.get("route_progress", {}).get("current_step") == "jurisdiction"):
                    self._start_wait("ask_user", {"questions": [{
                        "question_id": "litigation_intake", "field": "litigation_role",
                        "text": "你是准备起诉对方，还是收到了起诉材料要应诉？你希望解决什么问题？",
                    }]}, None, intake=True)
        return {"tasks": self.task_rows(), **({"jump_to": "end"} if self.cp.status != "running" else {})}

    def _first_route_material(self):
        cp = self.cp
        if (cp.stage != "litigation" or cp.conversation_route != "case_work"
                or not self.messages or self.messages[-1].type != "human"
                or (cp.routing_source != "litigation_entry" and not self.reply_files)
                or "parse_documents" not in self.business_tools
                or cp.config.enabled_tools is not None and "parse_documents" not in cp.config.enabled_tools):
            return None
        task = current(self.tasks)
        if not task or task.route_step not in ("jurisdiction", "evidence", "draft"):
            return None
        snapshot = self.cases.get_snapshot(cp.case_id).summary
        files = snapshot.get("recent_uploaded_files", [])
        if self.reply_files:
            files = [r for r in files if r["file_id"] in self.reply_files]
        elif len(files) != 1 or self.messages[-1].text != ENTRY_TEXT:
            return None  # With several existing files the model selects relevant materials.
        seen = {(ref["file_id"], ref["file_version"]) for t in self.tasks for key in t.input_refs
                for ref in [json.loads(key)]}
        versions = {r["file_id"]:r["file_version"] for r in files
                    if (r["file_id"], r["file_version"]) not in seen}
        if versions:
            return AIMessage(content="先读取本案材料，再核对仍需补充的信息。", name="litigation_intake",
                             tool_calls=[{"id": f"route_read_{cp.run_id}_{cp.step_id}", "name": "parse_documents",
                                          "args": {"file_ids": list(versions), "file_versions": versions}}])
        return None

    @hook_config(can_jump_to=["end"])
    def after_model(self, state, runtime):
        self.last_answer = latest_answer(state["messages"])
        if self._cancelled_or_ended():
            return {"jump_to": "end"}
        reply = state["messages"][-1]
        if not reply.tool_calls:
            if self._try_mediation_wait({}, self.model_step):
                return {"messages": [RemoveMessage(id=reply.id)], "jump_to": "end"}
            self.finish({"answer": reply.text or ""})
            return {"jump_to": "end"}
        return None

    def wrap_model_call(self, request, handler):
        cp, cfg = self.cp, self.cp.config
        snapshot = self.cases.get_snapshot(cp.case_id)
        cp.case_version, cp.stage = snapshot.case_version, snapshot.stage
        tools = [t for t in self.business_tools.values() if cp.stage in policy_of(t).stages
                 and (cfg.enabled_tools is None or t.name in cfg.enabled_tools)
                 and not self._invalid_calls_exhausted(t)] + [
            t for t in BUILTINS.values() if cp.stage in policy_of(t).stages]
        tools = [t for t in tools if tool_allowed(cp.conversation_route, t.name)
                 and allowed(t.name, cp.search_calls, cp.clarification_rounds, cfg)]
        names = {spec.name for spec in tools}
        messages = assemble(
            request.messages, snapshot, cfg.max_context_chars, route=cp.conversation_route,
            runtime_context={**limits(cp.search_calls, cp.clarification_rounds, cfg, cp.route_selected),
                             'remaining_budget': remaining_budget(cp),
                             'tasks': self.task_rows()},
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
            elif self._invalid_calls_exhausted(native_tool):
                result = ToolResult.fail("NOT_ALLOWED", f"{call.tool_name} 已达到参数纠错上限，请依据已有信息继续，必要时说明缺口。")
            elif loop and cp.feedback_rounds.get(loop, 0) >= limit:
                result = ToolResult.fail("NOT_ALLOWED", f"{loop} 已达到 {limit} 轮上限，请停止重试，在 finish 的 open_issues 中说明缺口。")
            elif not allowed(call.tool_name, cp.search_calls, cp.clarification_rounds, cp.config):
                result = ToolResult.fail("NOT_ALLOWED", "已达对话检索次数上限；请交付已有结果并说明未知项，不继续检索。")
            else:
                # Per-call immutable snapshot: no shared mutable runtime state for
                # timed-out workers. ToolNode injects this runtime into @tool.
                tool_request = replace(request, runtime=replace(request.runtime, context=ctx))

                def execute_native():
                    message = handler(tool_request)
                    return ToolResult.model_validate_json(message.content)

                execution = self.executor.execute(native_tool, call.tool_name, call.arguments, ctx,
                                                   execute_native, cp.config.enabled_tools)
                result, attempts = execution.result, execution.attempts
                invalid = result.error is not None and result.error.code == "INVALID_ARGS"
                if call.tool_name == "search_statutes" and attempts and not invalid:
                    cp.search_calls += 1
                if policy and policy.invalid_args_limit is not None:
                    key = f"invalid_args:{call.tool_name}"
                    if invalid:
                        cp.feedback_rounds[key] = cp.feedback_rounds.get(key, 0) + 1
                        if self._invalid_calls_exhausted(native_tool):
                            note = f"{call.tool_name} 参数连续未通过校验，请依据已有信息继续；必要时在回答中说明剩余缺口。"
                            cp.limit_notes.append(note)
                            result.error.details = {**(result.error.details or {}), "next_action": note}
                    elif result.status in ("ok", "insufficient", "empty"):
                        cp.feedback_rounds.pop(key, None)
                try:
                    refs = [SourceRef.model_validate(match["source_ref"])
                            for match in (result.data or {}).get("matches", [])]
                except (KeyError, TypeError, ValueError):
                    result = ToolResult.fail("INTERNAL", "工具返回的来源引用不符合约定。")
                if loop and result.status != "ok" and not (call.tool_name == "search_statutes" and invalid):
                    cp.feedback_rounds[loop] = cp.feedback_rounds.get(loop, 0) + 1
                    if cp.feedback_rounds[loop] == limit:
                        cp.limit_notes.append(f"检索与核验已重复 {limit} 轮仍未得到充分依据（{loop}），相关结论需进一步核实。")
            cp.usage.tool_calls += 1
            cp.usage.tool_attempts += attempts
            if result.case_version is not None:
                cp.case_version = result.case_version
            task = current(self.tasks) if cp.conversation_route == "case_work" else None
            if task:
                self._record_task_tool_result(task, call, result)
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

    def _record_task_tool_result(self, task, call, result):
        data = (result.data or {}).get("data") or {}
        if call.tool_name == "parse_documents" and (task.kind in ("general", "item_review", "evidence_directory")
                                                    or task.route_step == "draft"):
            unreadable = data.get("unreadable", {})
            if result.error:
                unreadable = (result.error.details or {}).get("unreadable", {})
            parsed = {row["file_id"] for row in data.get("documents", [])}
            # A declared file scope takes precedence. An item-only review can
            # still use extracted text without requiring unrelated pages.
            scope = set(task.review_file_ids) or (set() if task.review_items else parsed | unreadable.keys())
            for file_id in scope & (parsed | unreadable.keys()):
                task.unreadable_files.pop(file_id, None)
                if file_id in unreadable:
                    task.unreadable_files[file_id] = unreadable[file_id]
            for segment in data.get("text_segments", []):
                ref = segment["material_ref"]
                if task.review_file_ids and ref["file_id"] not in scope:
                    continue
                locator = {k: v for k, v in ref.items() if k in (
                    "file_id", "file_version", "extraction_version", "page", "paragraph", "span_start", "span_end"
                ) and v is not None}
                key = json.dumps(locator, ensure_ascii=False, sort_keys=True)
                if key not in task.input_refs:
                    task.input_refs.append(key)
        elif call.tool_name == "build_evidence_directory" and (task.route_step == "evidence" or task.kind == "evidence_directory"):
            refs = (result.data or {}).get("output_refs", [])
            if result.status in ("ok", "insufficient") and refs and task.kind in ("general", "evidence_directory"):
                task.kind, task.output_refs = "evidence_directory", list(refs)
        elif call.tool_name == "draft_document" and data.get("output_id"):
            self.draft_attempts[task.task_id] = data["output_id"]
            if task.route_step == "draft" and task.kind == "general":
                task.kind = "document"
            if task.kind == "document":
                reader = getattr(self.cases, "draft_missing_fields", None)
                if reader and reader(self.cp.case_id, data["output_id"], task.formats) is not None:
                    task.output_refs = [data["output_id"]]
        elif call.tool_name == "calculate_costs" and task.kind in ("general", "calculation"):
            calculation_type = call.arguments.get("calculation_type")
            if not calculation_type or (task.calculation_type and calculation_type != task.calculation_type):
                return
            try:
                calculated = CalculationData.model_validate(data) if data else None
            except ValidationError:
                calculated = None
            if calculated is not None and calculated.calculation_type != calculation_type:
                calculated = None
            self.calculation_results.setdefault(task.task_id, {})[calculation_type] = (result.case_version, calculated)

    def _calculation_delivery(self, task, case_version):
        results = self.calculation_results.get(task.task_id, {})
        calculation_type = task.calculation_type
        if calculation_type is None and len(results) == 1:
            calculation_type = next(iter(results))
        version, calculation = results.get(calculation_type, (None, None))
        if calculation is None or version != case_version:
            return "", ["尚未取得针对当前案情的可核验计算结果"]
        amount_names = {
            "total": "合计", "remaining_principal": "剩余本金", "principal": "本金",
            "repaid_principal": "已归还本金", "fee_exact": "按公式计算的费用", "fee_estimate": "预估费用",
        }
        amounts = "；".join(f"{amount_names.get(key, key)}：{value.amount} {value.currency}"
                           for key, value in calculation.amounts.items())
        missing = [f"计算待确认：{item}" for item in calculation.unresolved_inputs]
        if not amounts and not missing:
            missing.append("计算工具未返回可交付的金额")
        return amounts, missing

    @staticmethod
    def _material_gaps(task, refs):
        gaps = [gap for file_id, rows in task.unreadable_files.items()
                if not task.review_file_ids or file_id in task.review_file_ids for gap in rows]
        parsed_ids = {json.loads(ref)["file_id"] for ref in refs}
        gaps.extend(f"{file_id}：尚未读取需核对的材料。" for file_id in task.review_file_ids
                    if file_id not in parsed_ids and file_id not in task.unreadable_files)
        return gaps

    def _document_notices(self, task):
        candidate = self.draft_attempts.get(task.task_id)
        refs = [candidate] if candidate else task.output_refs
        reader = getattr(self.cases, "draft_missing_fields", None)
        task.output_refs = []
        notices = []
        if reader:
            for ref in refs:
                fields = reader(self.cp.case_id, ref, task.formats)
                if fields is not None:
                    task.output_refs.append(ref)
                    notices.extend(f"文书待补或复核：{field}" for field in fields)
        return notices

    def _control(self, call: ToolCall, request, handler) -> ToolResult:
        cp = self.cp
        result = validate_call(BUILTINS[call.tool_name], call.arguments, cp.stage)
        if result is None and not allowed(call.tool_name, cp.search_calls, cp.clarification_rounds, cp.config):
            result = ToolResult.fail("NOT_ALLOWED", "已达事实追问轮数上限；请根据已有信息回答并列出未知项，仍可提供路线选择。")
        if result is None and call.tool_name == "ask_user":
            questions = call.arguments.get("questions", [])
            repeated = any(q["text"].strip() in cp.asked_questions for q in questions)
            labels = [q["gap"].strip() for q in questions if q.get("gap")]
            task = current(self.tasks) if cp.conversation_route == "case_work" else None
            change = call.arguments.get("task_change") or {}
            if change.get("action", "continue") == "continue":
                target = change.get("task_id") or (task.task_id if task else "")
                previous = cp.asked_gap_labels.get(target, [])
                repeated = repeated or any(label in previous for label in labels)
            if (len(questions) > cp.config.max_questions_per_round or repeated or len(labels) != len(set(labels))
                    or len({q["text"].strip() for q in questions}) != len(questions)
                    or len({q["question_id"] for q in questions}) != len(questions)):
                result = ToolResult.fail("INVALID_ARGS", f"每轮最多{cp.config.max_questions_per_round}个关键问题；已问过的问题不得原样重复，未知信息可保留后交付建议。")
        if result is None:
            # Native control tools return action arguments; state changes stay here.
            try:
                call.arguments = json.loads(handler(request).content)
            except Exception as exc:
                result = ToolResult.fail("INVALID_ARGS", f"Control tool failed ({type(exc).__name__})")
        if result is None and call.tool_name == "ask_user":
            confirmations = [q for q in call.arguments["questions"]
                             if (q.get("field") or "").startswith("litigation_feedback.")]
            if confirmations:
                event = confirmations[0]["field"].split(".", 1)[1]
                if (len(call.arguments["questions"]) != 1 or event not in EVENTS or cp.stage != "litigation"
                        or not getattr(self.cases, "litigation", None)):
                    result = ToolResult.fail("INVALID_ARGS", "请在诉讼路线中一次确认一项实际办理反馈。")
                else:
                    try:
                        self.cases.litigation.validate_feedback(self.cases.repository.get_case(cp.case_id), event)
                        confirmations[0]["text"] = f"是否确认记录「{EVENTS[event][2]}」？确定已发生请只答“确认”；其他回答作为情况说明。"
                        if confirmations[0]["text"] in cp.asked_questions:
                            result = ToolResult.fail("INVALID_ARGS", "本轮已经询问过此项办理反馈。")
                    except ValueError as exc:
                        result = ToolResult.fail("INVALID_ARGS", str(exc))
        if result is None:
            change = call.arguments.pop("task_change", None)
            if change and self.allow_task_change and cp.conversation_route == "case_work":
                user_text = next((m.text for m in reversed(self.messages) if m.type == "human"), "")
                intent = TaskIntent.model_validate(change)
                if cp.stage != "litigation":
                    intent.route_step = None
                select_task(self.tasks, intent, user_text)
                self.allow_task_change = False
        if result is None and call.tool_name == "finish":
            if self._try_reported_wait(call.arguments, self.model_step):
                return ToolResult(status="ok", summary="已向用户追问当前交付所需的信息。")
            if self._try_mediation_wait(call.arguments, self.model_step):
                return ToolResult(status="ok", summary="调解方案仍需确认关键付款信息，已向用户追问。")
            self.finish(call.arguments, self.model_step)
            return ToolResult(status="ok", summary="运行结束。")
        if result is None:
            self._start_wait(call.tool_name, call.arguments, self.model_step)
            return ToolResult(status="ok", summary="已发送给用户，等待回复。")
        with self.store.step(cp, "error", call.tool_name, call.arguments, self.model_step) as step:
            step.status, step.error, step.result_summary = result.status, result.error, result.summary
        return result

    def _try_reported_wait(self, args, parent):
        cp = self.cp
        task = current(self.tasks) if cp.conversation_route == "case_work" else None
        report = args.get("task_result") or {}
        questions = []
        if "gaps" in report:
            kinds, question_labels = {}, {}
            for item in report["gaps"]:
                label = item["label"].strip()
                if not label or label in kinds:
                    continue
                kinds[label] = item["kind"]
                text = item.get("question", "").strip()
                if item["kind"] == "user_input" and text and text not in question_labels:
                    question_labels[text] = label
                    questions.append({"text": text, "gap": label})
            report["missing"] = list(kinds)
            args["open_issues"] = list(dict.fromkeys([
                *(question_labels.get(i, i) for i in args.get("open_issues", [])), *kinds,
            ]))
            if task:
                task.missing_kinds = kinds
                task.missing = list(kinds)
        elif question := report.get("next_question", "").strip():
            # Older clients submitted an unlinked question as well as missing labels.
            # Preserve the labels once, without guessing which label the question answers.
            labels = report.get("missing", task.missing if task else []) or [question]
            report["missing"] = labels
            args["open_issues"] = list(dict.fromkeys([*args.get("open_issues", []), *labels]))
            questions.append({"text": question, "gap": question if question in labels else None})
        if (not questions or task and (task.mediation_preliminary or task.kind == "unsupported_document")
                or not allowed("ask_user", cp.search_calls, cp.clarification_rounds, cp.config)
                or cp.budget_reason(time.monotonic() - self.started_at)):
            return False
        asked = cp.asked_gap_labels.get(task.task_id if task else "", [])
        selected = [q for q in questions if q["text"] not in cp.asked_questions and q["gap"] not in asked]
        selected = selected[:cp.config.max_questions_per_round]
        if not selected:
            return False
        if task:
            task.missing = list(dict.fromkeys(report["missing"]))
        for n, q in enumerate(selected, 1):
            q["question_id"] = f"next_{cp.step_id + 1}_{n}"
        self._start_wait("ask_user", {"questions": selected}, parent)
        return True

    def _try_mediation_wait(self, args, parent):
        task = current(self.tasks) if self.cp.conversation_route == "case_work" else None
        if not task or task.kind != "mediation_plan":
            return False
        cp = self.cp
        quotes = (args.get("task_result") or {}).get("mediation_inputs", {})
        # Resumed user messages contain the system's question as well as the reply.
        user_texts = [re.sub(r"(?ms)^问：.*?\n答：", "", message.text)
                      for message in self.messages if message.type == "human"]
        for field, quote in quotes.items():
            sources = user_texts if field not in task.mediation_inputs else user_texts[-1:]
            if quote.strip() and any(quote in text for text in sources):
                record_mediation_answer(task, field, quote)
        if (not allowed("ask_user", cp.search_calls, cp.clarification_rounds, cp.config)
                or cp.budget_reason(time.monotonic() - self.started_at)):
            return False
        questions = [
            {"question_id": f"mediation_{field}", "field": field, "text": text,
             "reason": f"会影响调解方案中的{label}"}
            for field, (label, text) in mediation_gaps(task).items()
            if field not in task.mediation_asked and text not in cp.asked_questions
        ][:cp.config.max_questions_per_round]
        if questions:
            self._start_wait("ask_user", {"questions": questions}, parent)
        return bool(questions)

    def _start_wait(self, tool_name, args, parent, *, intake=False):
        cp = self.cp
        asking = tool_name == "ask_user"
        kind = "ask_user" if asking else "route_choice"
        with self.store.step(cp, kind, tool_name, args, parent) as step:
            wait_kind = "clarification" if asking else "route_selection"
            cp.wait = Wait(wait_id=f"wait_{cp.run_id}_{step.step_id}", **{"kind": wait_kind, **args})
            if asking:
                if intake:
                    cp.intake_wait_id = cp.wait.wait_id
                else:
                    cp.clarification_rounds += 1
                cp.asked_questions.extend(q.text.strip() for q in cp.wait.questions)
                task = current(self.tasks) if cp.conversation_route == "case_work" else None
                labels = [q.gap.strip() for q in cp.wait.questions if q.gap and q.gap.strip()]
                if labels:
                    key = task.task_id if task else ""
                    cp.asked_gap_labels[key] = list(dict.fromkeys([*cp.asked_gap_labels.get(key, []), *labels]))
                if task:
                    self._mark_task_waiting(task)
            step.status = "waiting"
            step.result_summary = "；".join(question.text for question in cp.wait.questions) or "请用户选择处理路线"
        cp.status = "waiting_user"
        self.store.emit(cp, "run.waiting_user", wait=cp.wait.model_dump())

    def _mark_task_waiting(self, task):
        cp = self.cp
        task.status = "waiting_input"
        gaps = task.verified_missing
        questions = []
        # Questions already live in wait.questions. The task tracks the underlying
        # issue, even after a wait is replaced or the task is temporarily paused.
        for q in cp.wait.questions:
            if q.field in MEDIATION_QUESTIONS and task.kind == "mediation_plan":
                continue
            label = (q.gap or "").strip()
            if label:
                questions.append(label)
                task.missing_kinds[label] = "material" if cp.wait.kind == "material_request" else "user_input"
            elif not task.missing and cp.intake_wait_id != cp.wait.wait_id:
                questions.append(q.text)  # legacy ask_user with no other description
        if task.kind == "item_review":
            gaps = self._material_gaps(task, task.input_refs)
        elif task.kind == "calculation" and task.task_id in self.calculation_results:
            _, gaps = self._calculation_delivery(task, cp.case_version)
        elif task.kind == "document" and task.document_scope == "complete":
            gaps = self._document_notices(task)
        elif task.kind == "mediation_plan":
            fields = [q.field for q in cp.wait.questions if q.field in MEDIATION_QUESTIONS]
            task.mediation_asked = list(dict.fromkeys([*task.mediation_asked, *fields]))
            gaps = [label for label, _ in mediation_gaps(task).values()]
        task.missing = list(dict.fromkeys([
            *(item for item in task.missing if item not in task.verified_missing),
            *gaps, *questions,
        ]))
        task.verified_missing = gaps
        task.missing_kinds = {k:v for k,v in task.missing_kinds.items() if k in task.missing}
        self._classify_verified_gaps(task)

    @staticmethod
    def _classify_verified_gaps(task):
        origin = {"item_review": "material", "mediation_plan": "user_input"}.get(task.kind, "verification")
        for gap in task.verified_missing:
            if gap in task.missing:
                task.missing_kinds.setdefault(gap, origin)

    def finish(self, args: dict, parent=None):
        with self.store.step(self.cp, "finish", "finish", parent=parent) as step:
            case_version = self.cases.get_snapshot(self.cp.case_id).case_version
            task = current(self.tasks) if self.cp.conversation_route == "case_work" else None
            if task:
                texts = []
                verified_missing, notices = [], []
                calculated = False
                if task.kind == "item_review":
                    refs = [ref for ref in task.input_refs
                            if not task.review_file_ids or json.loads(ref)["file_id"] in task.review_file_ids]
                    reader = getattr(self.cases, "extracted_text", None)
                    if reader:
                        texts = [reader(self.cp.case_id, ref) for ref in refs]
                    verified_missing = self._material_gaps(task, refs)
                if task.kind == "document":
                    notices = self._document_notices(task)
                    if task.document_scope == "complete":
                        verified_missing = notices
                if task.kind == "calculation":
                    amounts, verified_missing = self._calculation_delivery(task, case_version)
                    calculated = bool(amounts)
                    if calculated:
                        args = {**args, "answer": f"{args['answer']}\n\n按本次输入计算：{amounts}"}
                    else:
                        # Keep source statements and actionable gaps without presenting a
                        # model-computed number as an independently verified total.
                        report = args.get("task_result") or {}
                        gaps = list(dict.fromkeys([*report.get("missing", task.missing), *verified_missing]))
                        answer = self._incomplete_answer("本轮无法确认所请求的金额。", report, gaps)
                        args = {**args, "answer": answer, "citations": [], "output_refs": []}
                if task.kind == "mediation_plan":
                    verified_missing = [label for label, _ in mediation_gaps(task).values()]
                if task.kind == "evidence_directory":
                    reader = getattr(self.cases, "directory_gaps", None)
                    refs = []
                    for ref in task.output_refs:
                        gaps = reader(self.cp.case_id, ref) if reader else None
                        if gaps is not None:
                            refs.append(ref)
                            verified_missing.extend(gaps)
                    task.output_refs = refs
                    args = {**args, "output_refs": refs}
                delivered = assess(task, args, self.messages, texts, self.reply_evidence,
                                   verified_missing=verified_missing, calculated=calculated)
                self._classify_verified_gaps(task)
                args = {**args, "open_issues": list(dict.fromkeys([
                    *args.get("open_issues", []), *task.missing_kinds, *verified_missing, *notices,
                ]))}
                if delivered:
                    task.delivery_run_id = self.cp.run_id
                if task.kind in ("document", "unsupported_document") and not task.output_refs:
                    args = {**args, "answer": self._incomplete_answer("本轮没有可交付的文书文件。",
                                args.get("task_result") or {}, task.missing),
                            "output_refs": [], "citations": [], "open_issues": list(dict.fromkeys(
                                [*args.get("open_issues", []), *task.missing]))}
                elif task.kind == "document":
                    args = {**args, "output_refs": task.output_refs}
            self.cp.complete(args, case_version)
            step.result_summary = self.cp.final.answer[:200]
        return self.cp.info()

    def _incomplete_answer(self, heading, report, gaps):
        # Return original lines, so extracting "有凭证" from "没有凭证" cannot
        # invert the user's meaning. Do not present the assistant's question as input.
        sources = [line.removeprefix("答：").strip() for m in self.messages if m.type == "human"
                   for line in re.sub(r"(?ms)^问：.*?\n答：", "", m.text).splitlines()]
        quotes = report.get("known_inputs") or self.reply_evidence
        inputs = list(dict.fromkeys(line for line in sources if 0 < len(line) <= 500
                                    and any(q.strip() and q.strip() in line for q in quotes)))[:6]
        if inputs:
            heading += "\n你提供的信息：\n" + "\n".join(f"· {item}" for item in inputs)
        return heading + "\n仍需核对：" + "；".join(gaps)

    @staticmethod
    def _message(call_id, name, result):
        return ToolMessage(content=result.model_dump_json(exclude_none=True), tool_call_id=call_id,
                           name=name, status="error" if result.status == "error" else "success")
