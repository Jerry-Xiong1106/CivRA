"""Agent runner: the main loop plus start / resume / cancel / trace export (contract §4–§7).

start_run and resume_run are synchronous: they drive the run until it waits for
the user or ends. The API layer can call them in a worker thread and stream
progress with iter_events.
"""

from __future__ import annotations

import hashlib
import json
import time
import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any, Iterator

import jsonschema

from backend.agent.builtins import BUILTINS
from backend.agent.case_port import CaseStatePort
from backend.agent.llm import LLMClient
from backend.agent.prompts import SYSTEM_PROMPT, assemble
from backend.agent.store import Checkpoint, RunStore, dump
from backend.common.schemas import (
    TERMINAL_STATUSES, Citation, FinalOutput, LLMResponse, RunConfig, RunEvent, RunInfo,
    RuntimeManifest, SourceRef, ToolCall, ToolContext, ToolError, ToolManifestEntry, ToolResult,
    TraceExport, TraceStep, UserReply, Wait, WaitOption, WaitQuestion, now_iso,
)
from backend.tools.registry import ToolRegistry

RUNTIME_VERSION = "0.5.0"


class RunConflict(Exception):
    """Another run of the same case is already running (HTTP 409)."""


class RunStateError(Exception):
    """The requested operation does not fit the run's current status."""


class StaleWait(RunStateError):
    """resume_run quoted a wait_id that is not the run's current wait."""


class VersionConflict(RunStateError):
    """expected_case_version does not match the case's current version."""


class AgentRunner:
    def __init__(
        self,
        llm: LLMClient,
        registry: ToolRegistry,
        cases: CaseStatePort,
        store: RunStore | None = None,
        default_config: RunConfig | None = None,
    ) -> None:
        self.llm = llm
        self.registry = registry
        self.cases = cases
        self.store = store or RunStore()
        self.default_config = default_config or RunConfig()
        self._cancel_requested: set[str] = set()

    # ------------------------------------------------------------ public API

    def start_run(
        self,
        case_id: str,
        user_message: str,
        *,
        config: RunConfig | None = None,
        experiment_run_id: str | None = None,
        request_id: str | None = None,
    ) -> RunInfo:
        """Start a run. Repeating a request_id returns the run it already started."""
        if request_id is not None:
            for existing in self.store.list_checkpoints():
                if existing.start_request_id == request_id:
                    return self.get_run(existing.run_id)
        self._ensure_not_running(case_id)
        snap = self.cases.get_snapshot(case_id)
        cp = Checkpoint(
            run_id=f"run_{datetime.now():%Y%m%d}_{uuid.uuid4().hex[:6]}",
            case_id=case_id,
            status="running",
            config=config or self.default_config,
            messages=[{"role": "user", "content": user_message}],
            case_version=snap.case_version,
            stage=snap.stage,
            experiment_run_id=experiment_run_id,
            start_request_id=request_id,
        )
        self._emit(cp, "run.started", data={
            "case_id": case_id, "case_version": snap.case_version, "mode": cp.config.mode,
            "experiment_run_id": experiment_run_id,
        })
        return self._drive(cp)

    def resume_run(
        self,
        run_id: str,
        reply: UserReply,
        *,
        wait_id: str,
        expected_case_version: int | None = None,
        request_id: str | None = None,
    ) -> RunInfo:
        """Answer the run's current wait and continue.

        Raises StaleWait / VersionConflict without executing anything. Repeating a
        request_id that was already applied returns the current state instead of
        running again, so a client can safely retry after a timeout.
        """
        cp = self.store.load_checkpoint(run_id)
        if request_id is not None and request_id in cp.applied_requests:
            return self.get_run(run_id)
        if cp.status != "waiting_user" or cp.wait is None:
            raise RunStateError(f"run {run_id} is {cp.status}, not waiting_user")
        if wait_id != cp.wait.wait_id:
            raise StaleWait(f"wait_id {wait_id} is stale; current wait is {cp.wait.wait_id}")
        current_version = self.cases.get_snapshot(cp.case_id).case_version
        if expected_case_version is not None and expected_case_version != current_version:
            raise VersionConflict(f"expected case_version {expected_case_version}, case is at {current_version}")
        self._ensure_not_running(cp.case_id)
        wait = cp.wait

        if wait.kind == "route_selection":
            allowed = [o.route for o in wait.options]
            if reply.route_choice not in allowed:
                raise RunStateError(f"route_choice must be one of {allowed}")
        if request_id is not None:
            cp.applied_requests.append(request_id)

        if wait.kind == "route_selection":
            started, t0 = now_iso(), time.monotonic()
            cp.case_version = self.cases.set_stage(cp.case_id, reply.route_choice)
            cp.stage = reply.route_choice
            step_id = self._begin_step(cp, "route_choice", "request_route_choice", {"route_choice": reply.route_choice})
            self._finish_step(cp, step_id, "route_choice", started, t0, status="ok", tool_name="request_route_choice",
                              arguments={"route_choice": reply.route_choice},
                              result_summary=f"用户选择路线：{reply.route_choice}")

        cp.messages.append({"role": "user", "content": _reply_text(reply, wait)})
        cp.status, cp.wait = "running", None
        # New information from the user opens a fresh retrieve/verify budget.
        cp.feedback_rounds, cp.limit_notes = {}, []
        self._emit(cp, "run.resumed", data={"wait_id": wait_id, "request_id": request_id,
                                            "reply": reply.model_dump(exclude_defaults=True)})
        return self._drive(cp)

    def cancel_run(self, run_id: str, *, request_id: str | None = None) -> RunInfo:
        cp = self.store.load_checkpoint(run_id)
        if cp.status in TERMINAL_STATUSES:
            return self.get_run(run_id)
        if cp.status == "waiting_user":
            return self._stop(cp, "cancelled", "用户取消")
        # running: picked up by the loop before its next step (same process only)
        self._cancel_requested.add(run_id)
        cp.cancel_requested = True
        self.store.save_checkpoint(cp)
        return self.get_run(run_id)

    def get_run(self, run_id: str) -> RunInfo:
        cp = self.store.load_checkpoint(run_id)
        info = _info(cp)
        if cp.final is not None:
            info.stale = self.cases.get_snapshot(cp.case_id).case_version > cp.final.case_version
        return info

    def iter_events(self, run_id: str, after_seq: int = 0) -> Iterator[RunEvent]:
        yield from self.store.read_events(run_id, after_seq)

    def get_trace(self, run_id: str) -> list[TraceStep]:
        return self.store.read_trace(run_id)

    def export_trace(self, run_id: str, cursor: int = 0, limit: int = 100) -> TraceExport:
        """Page through trace steps in step order. cursor = number of steps already read."""
        cp = self.store.load_checkpoint(run_id)
        steps = self.store.read_trace(run_id)
        page = steps[cursor:cursor + limit]
        end = cursor + len(page)
        more_stored = end < len(steps)
        return TraceExport(
            run_id=run_id,
            steps=page,
            next_cursor=end if (more_stored or cp.status not in TERMINAL_STATUSES) else None,
            complete=cp.status in TERMINAL_STATUSES and not more_stored,
        )

    def get_step_result(self, run_id: str, result_ref: str) -> dict[str, Any]:
        """Full input and result of a step, as referenced by TraceStep.result_ref."""
        return self.store.read_result(run_id, result_ref)

    def get_runtime_manifest(self) -> RuntimeManifest:
        specs = sorted([*self.registry.all_specs(), *BUILTINS.values()], key=lambda s: s.name)
        entries = [
            ToolManifestEntry(name=s.name, owner=s.owner, stages=s.stages, schema_sha256=_sha256(s.model_dump()))
            for s in specs
        ]
        return RuntimeManifest(
            runtime_version=RUNTIME_VERSION,
            prompt_sha256=_sha256(SYSTEM_PROMPT),
            tool_schema_sha256=_sha256([e.model_dump() for e in entries]),
            tools=entries,
            model={"client": type(self.llm).__name__, "default_model": getattr(self.llm, "model", None)},
            default_config=self.default_config,
        )

    # ------------------------------------------------------------ main loop

    def _drive(self, cp: Checkpoint) -> RunInfo:
        t0 = time.monotonic()
        try:
            return self._loop(cp, t0)
        finally:
            cp.usage.duration_ms += int((time.monotonic() - t0) * 1000)
            self.store.save_checkpoint(cp)

    def _loop(self, cp: Checkpoint, t0: float) -> RunInfo:
        cfg = cp.config
        while True:
            if cp.run_id in self._cancel_requested:
                self._cancel_requested.discard(cp.run_id)
                return self._stop(cp, "cancelled", "用户取消")
            if cp.usage.steps >= cfg.max_steps:
                return self._stop(cp, "budget_exhausted", f"达到步数上限（{cfg.max_steps} 步）")
            if cfg.max_model_calls is not None and cp.usage.model_calls >= cfg.max_model_calls:
                return self._stop(cp, "budget_exhausted", f"达到模型调用上限（{cfg.max_model_calls} 次）")
            if cp.usage.tool_calls >= cfg.max_tool_calls:
                return self._stop(cp, "budget_exhausted", f"达到工具调用上限（{cfg.max_tool_calls} 次）")
            used_tokens = cp.usage.prompt_tokens + cp.usage.completion_tokens
            if cfg.max_total_tokens is not None and used_tokens >= cfg.max_total_tokens:
                return self._stop(cp, "budget_exhausted", f"达到 token 上限（已用 {used_tokens}，上限 {cfg.max_total_tokens}）")
            active_s = cp.usage.duration_ms / 1000 + (time.monotonic() - t0)
            if cfg.max_run_seconds is not None and active_s >= cfg.max_run_seconds:
                return self._stop(cp, "budget_exhausted", f"达到时间上限（{cfg.max_run_seconds} 秒）")

            snap = self.cases.get_snapshot(cp.case_id)
            cp.case_version, cp.stage = snap.case_version, snap.stage
            tools = self.registry.available(cp.stage, cfg.enabled_tools) + [
                s for s in BUILTINS.values() if cp.stage in s.stages
            ]

            model_step = cp.step_id + 1
            resp = self._call_model(cp, assemble(cp.messages, snap, cfg.max_context_chars), tools)
            if resp is None:
                return _info(cp)  # _call_model already ended the run as failed
            cp.messages.append(_assistant_message(resp))

            if not resp.tool_calls:
                # Plain text with no tool call is treated as the final answer.
                return self._finish(cp, {"answer": resp.content or ""})

            outcome: str | None = None  # "waiting" or "finished" once a control action ran
            for call in resp.tool_calls:
                if outcome is not None:
                    # Every tool_call_id needs a reply in the OpenAI format.
                    cp.messages.append(_tool_message(call.id, ToolResult(status="error", summary="未执行：运行已暂停或结束。")))
                    continue
                if call.tool_name in BUILTINS:
                    outcome = self._handle_builtin(cp, call, model_step)
                else:
                    self._handle_tool(cp, call, model_step)

            if outcome == "waiting":
                cp.status = "waiting_user"
                self._emit(cp, "run.waiting_user", data={"wait": cp.wait.model_dump()})
                return _info(cp)
            if outcome == "finished":
                return _info(cp)
            self.store.save_checkpoint(cp)

    def _call_model(self, cp: Checkpoint, messages: list[dict], tools: list) -> LLMResponse | None:
        cfg = cp.config
        step_id = self._begin_step(cp, "model_call", None, None)
        started, t0 = now_iso(), time.monotonic()
        params = {"model": cfg.model, "temperature": cfg.temperature, "max_tokens": cfg.max_output_tokens,
                  "seed": cfg.seed}
        try:
            resp = self.llm.chat(messages, tools, **params)
        except Exception as e:
            err = ToolError(code="UPSTREAM", message=f"{type(e).__name__}: {e}")
            self._finish_step(cp, step_id, "model_call", started, t0, status="error", error=err,
                              arguments=params, result_summary=err.message)
            self._stop(cp, "failed", err.message)
            return None

        u = cp.usage
        u.model_calls += 1
        if resp.usage.prompt_tokens is None or resp.usage.completion_tokens is None:
            u.tokens_complete = False
        u.prompt_tokens += resp.usage.prompt_tokens or 0
        u.completion_tokens += resp.usage.completion_tokens or 0
        if resp.cost is None or u.estimated_cost is None:
            u.estimated_cost = None
        else:
            u.estimated_cost = str(Decimal(u.estimated_cost) + Decimal(resp.cost.amount))

        ref = self.store.save_result(cp.run_id, step_id, {
            "request": {**params, "tools": [t.name for t in tools], "message_count": len(messages)},
            "response": resp.model_dump(),
        })
        summary = ", ".join(c.tool_name for c in resp.tool_calls) or "回答"
        self._finish_step(cp, step_id, "model_call", started, t0, status="ok", arguments=params,
                          result_summary=f"模型选择：{summary}", result_ref=ref,
                          token_usage=resp.usage, cost=resp.cost, decision_note=(resp.content or None))
        return resp

    def _handle_tool(self, cp: Checkpoint, call: ToolCall, parent: int) -> None:
        step_id = self._begin_step(cp, "tool_call", call.tool_name, call.arguments)
        started, t0 = now_iso(), time.monotonic()
        spec = self.registry.get_spec(call.tool_name)
        timeout = spec.timeout_s if spec else 30.0
        ctx = ToolContext(
            case_id=cp.case_id, run_id=cp.run_id, step_id=step_id, case_version=cp.case_version,
            deadline=datetime.now(timezone.utc) + timedelta(seconds=timeout), stage=cp.stage,
        )
        loop = spec.feedback_loop if spec else None
        limit = cp.config.max_feedback_rounds
        if loop and cp.feedback_rounds.get(loop, 0) >= limit:
            # Not executed: tell the model to stop retrying and report the gap instead.
            result, attempts = ToolResult.fail(
                "NOT_ALLOWED", f"{loop} 已达到 {limit} 轮上限，请停止重试，在 finish 的 open_issues 中说明缺口。"
            ), 0
        else:
            ex = self.registry.execute(call.tool_name, call.arguments, ctx, cp.config.enabled_tools)
            result, attempts = ex.result, ex.attempts
            if loop and result.status != "ok":
                cp.feedback_rounds[loop] = cp.feedback_rounds.get(loop, 0) + 1
                if cp.feedback_rounds[loop] == limit:
                    cp.limit_notes.append(f"检索与核验已重复 {limit} 轮仍未得到充分依据（{loop}），相关结论需进一步核实。")
        cp.usage.tool_calls += 1
        cp.usage.tool_attempts += attempts
        if result.case_version is not None:
            cp.case_version = result.case_version
        _merge_sources(cp.source_refs, result.source_refs)
        ref = self.store.save_result(cp.run_id, step_id, {
            "tool_name": call.tool_name, "arguments": call.arguments, "attempts": attempts,
            "result": result.model_dump(),
        })
        self._finish_step(cp, step_id, "tool_call", started, t0, status=result.status, tool_name=call.tool_name,
                          parent_step_id=parent, arguments=call.arguments, result_summary=result.summary,
                          result_ref=ref, source_refs=result.source_refs, error=result.error,
                          attempt=max(attempts, 1))
        cp.messages.append(_tool_message(call.id, result))

    def _handle_builtin(self, cp: Checkpoint, call: ToolCall, parent: int) -> str | None:
        spec = BUILTINS[call.tool_name]
        try:
            jsonschema.validate(call.arguments, spec.parameters)
        except jsonschema.ValidationError as e:
            started, t0 = now_iso(), time.monotonic()
            step_id = self._begin_step(cp, "error", call.tool_name, call.arguments)
            err = ToolError(code="INVALID_ARGS", message=e.message)
            self._finish_step(cp, step_id, "error", started, t0, status="error", parent_step_id=parent,
                              tool_name=call.tool_name, arguments=call.arguments, error=err, result_summary=e.message)
            cp.messages.append(_tool_message(call.id, ToolResult.fail("INVALID_ARGS", e.message)))
            return None

        if call.tool_name == "finish":
            cp.messages.append(_tool_message(call.id, ToolResult(status="ok", summary="运行结束。")))
            self._finish(cp, call.arguments, parent)
            return "finished"

        started, t0 = now_iso(), time.monotonic()
        step_type = "ask_user" if call.tool_name == "ask_user" else "route_choice"
        step_id = self._begin_step(cp, step_type, call.tool_name, call.arguments)
        wait_id = f"wait_{cp.run_id}_{step_id}"
        args = call.arguments
        if call.tool_name == "ask_user":
            cp.wait = Wait(
                wait_id=wait_id, kind=args.get("kind", "clarification"),
                questions=[WaitQuestion(**q) for q in args["questions"]],
            )
            summary = "；".join(q["text"] for q in args["questions"])
        else:  # request_route_choice
            cp.wait = Wait(
                wait_id=wait_id, kind="route_selection", comparison=args["comparison"],
                options=[WaitOption(**o) for o in args["options"]],
            )
            summary = "请用户选择处理路线"
        self._finish_step(cp, step_id, step_type, started, t0, status="waiting", parent_step_id=parent,
                          tool_name=call.tool_name, arguments=args, result_summary=summary)
        cp.messages.append(_tool_message(call.id, ToolResult(status="ok", summary="已发送给用户，等待回复。")))
        return "waiting"

    # ------------------------------------------------------------ endings

    def _finish(self, cp: Checkpoint, args: dict[str, Any], parent: int | None = None) -> RunInfo:
        snap = self.cases.get_snapshot(cp.case_id)
        issues = list(args.get("open_issues", []))
        issues += [n for n in cp.limit_notes if n not in issues]
        known = {s.source_id for s in cp.source_refs}
        citations = [Citation(**c) for c in args.get("citations", [])]
        unknown = sorted({c.source_id for c in citations} - known)
        if unknown:
            # Only sources a tool actually returned may be cited.
            citations = [c for c in citations if c.source_id in known]
            issues.append(f"回答引用了未经检索返回的来源，已移除：{', '.join(unknown)}")
        cp.final = FinalOutput(
            answer=args.get("answer", ""),
            output_refs=args.get("output_refs", []),
            source_refs=cp.source_refs,
            citations=citations,
            open_issues=issues,
            case_version=snap.case_version,
        )
        started, t0 = now_iso(), time.monotonic()
        step_id = self._begin_step(cp, "finish", "finish", None)
        self._finish_step(cp, step_id, "finish", started, t0, status="ok", parent_step_id=parent,
                          tool_name="finish", result_summary=cp.final.answer[:200])
        return self._end(cp, "completed", "run.completed", final=cp.final.model_dump())

    def _stop(self, cp: Checkpoint, status: str, reason: str) -> RunInfo:
        """End a run early (budget, failure, cancel), keeping what was done as a partial output."""
        last_text = next((m["content"] for m in reversed(cp.messages)
                          if m["role"] == "assistant" and m.get("content")), None)
        answer = {"budget_exhausted": "已达到本次运行的预算上限。", "failed": "运行出错，未能完成。",
                  "cancelled": "运行已取消。"}[status]
        cp.final = FinalOutput(
            answer=answer + (f"\n\n已完成部分：\n{last_text}" if last_text else ""),
            source_refs=cp.source_refs,
            open_issues=[f"{reason}，任务未完成。", *cp.limit_notes],
            case_version=cp.case_version,
            completion_reason=status,
            partial=True,
        )
        event = f"run.{status}"
        return self._end(cp, status, event, reason=reason, final=cp.final.model_dump())

    def _end(self, cp: Checkpoint, status: str, event: str, **data: Any) -> RunInfo:
        cp.status, cp.wait = status, None
        if status == "failed":
            cp.error = data.get("reason")
        self._emit(cp, event, data=data)
        self.store.save_checkpoint(cp)
        return _info(cp)

    # ------------------------------------------------------------ records

    def _ensure_not_running(self, case_id: str) -> None:
        for other in self.store.list_checkpoints():
            if other.case_id == case_id and other.status == "running":
                raise RunConflict(f"case {case_id} already has a running run: {other.run_id}")

    def _emit(self, cp: Checkpoint, type_: str, step_id: int | None = None, data: dict | None = None) -> None:
        cp.event_seq += 1
        self.store.append_event(RunEvent(seq=cp.event_seq, run_id=cp.run_id, type=type_, step_id=step_id, data=data or {}))

    def _begin_step(self, cp: Checkpoint, step_type: str, tool_name: str | None, arguments: dict | None) -> int:
        cp.step_id += 1
        cp.usage.steps += 1
        self._emit(cp, "step.started", cp.step_id, {"step_type": step_type, "tool_name": tool_name, "arguments": arguments})
        return cp.step_id

    def _finish_step(self, cp: Checkpoint, step_id: int, step_type: str, started: str, t0: float, *,
                     status: str, **fields: Any) -> None:
        duration = int((time.monotonic() - t0) * 1000)
        self.store.append_step(TraceStep(
            event_id=f"{cp.run_id}:{step_id}", case_id=cp.case_id, run_id=cp.run_id, step_id=step_id,
            step_type=step_type, status=status, started_at=started, finished_at=now_iso(),
            duration_ms=duration, case_version=cp.case_version, **fields,
        ))
        err = fields.get("error")
        self._emit(cp, "step.finished", step_id, {
            "status": status, "summary": fields.get("result_summary", ""), "duration_ms": duration,
            "error": err.model_dump() if err else None,
        })


# ---------------------------------------------------------------- helpers

def _info(cp: Checkpoint) -> RunInfo:
    return RunInfo(run_id=cp.run_id, case_id=cp.case_id, case_version=cp.case_version,
                   experiment_run_id=cp.experiment_run_id, status=cp.status, wait=cp.wait,
                   final=cp.final, usage=cp.usage, error=cp.error)


def _sha256(obj: Any) -> str:
    text = obj if isinstance(obj, str) else json.dumps(obj, ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(text.encode()).hexdigest()


def _assistant_message(resp: LLMResponse) -> dict[str, Any]:
    msg: dict[str, Any] = {"role": "assistant", "content": resp.content}
    if resp.tool_calls:
        msg["tool_calls"] = [
            {"id": c.id, "type": "function", "function": {"name": c.tool_name, "arguments": dump(c.arguments)}}
            for c in resp.tool_calls
        ]
    return msg


def _tool_message(call_id: str, result: ToolResult) -> dict[str, Any]:
    return {"role": "tool", "tool_call_id": call_id, "content": result.model_dump_json(exclude_none=True)}


def _reply_text(reply: UserReply, wait: Wait) -> str:
    lines: list[str] = []
    questions = {q.question_id: q.text for q in wait.questions}
    for qid, answer in reply.answers.items():
        lines.append(f"问：{questions.get(qid, qid)}\n答：{answer}")
    if reply.route_choice:
        lines.append(f"用户选择的处理路线：{reply.route_choice}")
    if reply.file_ids:
        lines.append(f"用户补充上传了材料：{', '.join(reply.file_ids)}")
    if reply.text:
        lines.append(reply.text)
    return "\n".join(lines) or "（用户未提供内容）"


def _merge_sources(into: list[SourceRef], new: list[SourceRef]) -> None:
    seen = {s.source_id for s in into}
    into.extend(s for s in new if s.source_id not in seen and not seen.add(s.source_id))
