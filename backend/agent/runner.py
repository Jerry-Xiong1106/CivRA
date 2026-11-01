"""Agent runner: the main loop plus start / resume / cancel (contract §4, §6).

A run is driven synchronously until it waits for the user or ends. The API
layer can call start_run / resume_run in a worker thread and stream progress
with iter_events.
"""

from __future__ import annotations

import time
import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any, Iterator

import jsonschema

from backend.agent.builtins import BUILTINS
from backend.agent.case_port import CaseStatePort
from backend.agent.llm import LLMClient
from backend.agent.prompts import assemble
from backend.agent.store import Checkpoint, RunStore, dump
from backend.common.schemas import (
    TERMINAL_STATUSES, FinalOutput, LLMResponse, RunConfig, RunEvent, RunInfo, SourceRef,
    ToolCall, ToolContext, ToolError, ToolResult, TraceStep, UserReply, now_iso,
)
from backend.tools.registry import ToolRegistry


class RunConflict(Exception):
    """Another run of the same case is already running (HTTP 409)."""


class RunStateError(Exception):
    """The requested operation does not fit the run's current status."""


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

    def start_run(self, case_id: str, user_message: str, *, config: RunConfig | None = None) -> RunInfo:
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
        )
        self._emit(cp, "run.started", data={"case_id": case_id, "case_version": snap.case_version, "mode": cp.config.mode})
        return self._drive(cp)

    def resume_run(self, run_id: str, reply: UserReply) -> RunInfo:
        cp = self.store.load_checkpoint(run_id)
        if cp.status != "waiting_user":
            raise RunStateError(f"run {run_id} is {cp.status}, not waiting_user")
        self._ensure_not_running(cp.case_id)
        pending = cp.pending or {}

        if pending.get("type") == "route_choice":
            allowed = [o["route"] for o in pending.get("options", [])]
            if reply.route_choice not in allowed:
                raise RunStateError(f"route_choice must be one of {allowed}")
            started = now_iso()
            cp.case_version = self.cases.set_stage(cp.case_id, reply.route_choice)
            cp.stage = reply.route_choice
            self._record(cp, "route_choice", started, status="ok", tool_name="request_route_choice",
                         arguments={"route_choice": reply.route_choice},
                         result_summary=f"用户选择路线：{reply.route_choice}")

        cp.messages.append({"role": "user", "content": _reply_text(reply, pending)})
        cp.status, cp.pending = "running", None
        self._emit(cp, "run.resumed", data={"reply": reply.model_dump(exclude_defaults=True)})
        return self._drive(cp)

    def cancel_run(self, run_id: str) -> RunInfo:
        cp = self.store.load_checkpoint(run_id)
        if cp.status in TERMINAL_STATUSES:
            return _info(cp)
        if cp.status == "waiting_user":
            return self._end(cp, "cancelled", "run.cancelled", reason="用户取消")
        # running: picked up by the loop before its next step (same process only)
        self._cancel_requested.add(run_id)
        cp.cancel_requested = True
        self.store.save_checkpoint(cp)
        return _info(cp)

    def get_run(self, run_id: str) -> RunInfo:
        return _info(self.store.load_checkpoint(run_id))

    def iter_events(self, run_id: str, after_seq: int = 0) -> Iterator[RunEvent]:
        yield from self.store.read_events(run_id, after_seq)

    def get_trace(self, run_id: str) -> list[TraceStep]:
        return self.store.read_trace(run_id)

    # ------------------------------------------------------------ main loop

    def _drive(self, cp: Checkpoint) -> RunInfo:
        t0 = time.monotonic()
        try:
            return self._loop(cp)
        finally:
            cp.usage.duration_ms += int((time.monotonic() - t0) * 1000)
            self.store.save_checkpoint(cp)

    def _loop(self, cp: Checkpoint) -> RunInfo:
        cfg = cp.config
        while True:
            if cp.run_id in self._cancel_requested:
                self._cancel_requested.discard(cp.run_id)
                return self._end(cp, "cancelled", "run.cancelled", reason="用户取消")
            if cp.usage.steps >= cfg.max_steps or cp.usage.tool_calls >= cfg.max_tool_calls:
                return self._budget_exhausted(cp)

            snap = self.cases.get_snapshot(cp.case_id)
            cp.case_version, cp.stage = snap.case_version, snap.stage
            tools = self.registry.available(cp.stage, cfg.enabled_tools) + [
                s for s in BUILTINS.values() if cp.stage in s.stages
            ]

            resp = self._call_model(cp, assemble(cp.messages, snap), tools)
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
                    outcome = self._handle_builtin(cp, call)
                else:
                    self._handle_tool(cp, call)

            if outcome == "waiting":
                cp.status = "waiting_user"
                self._emit(cp, "run.waiting_user", data={"pending": cp.pending})
                return _info(cp)
            if outcome == "finished":
                return _info(cp)
            self.store.save_checkpoint(cp)

    def _call_model(self, cp: Checkpoint, messages: list[dict], tools: list) -> LLMResponse | None:
        step_id = self._begin_step(cp, "model_call", None, None)
        started, t0 = now_iso(), time.monotonic()
        try:
            resp = self.llm.chat(messages, tools, temperature=cp.config.temperature)
        except Exception as e:
            err = ToolError(code="UPSTREAM", message=f"{type(e).__name__}: {e}")
            self._finish_step(cp, step_id, "model_call", started, t0, status="error", error=err, result_summary=err.message)
            self._end(cp, "failed", "run.failed", reason=err.message)
            return None
        cp.usage.model_calls += 1
        cp.usage.prompt_tokens += resp.usage.prompt_tokens
        cp.usage.completion_tokens += resp.usage.completion_tokens
        cp.usage.estimated_cost = str(Decimal(cp.usage.estimated_cost) + Decimal(resp.estimated_cost))
        summary = ", ".join(c.tool_name for c in resp.tool_calls) or "回答"
        self._finish_step(cp, step_id, "model_call", started, t0, status="ok", result_summary=f"模型选择：{summary}",
                          token_usage=resp.usage, estimated_cost=resp.estimated_cost,
                          decision_note=(resp.content or None))
        return resp

    def _handle_tool(self, cp: Checkpoint, call: ToolCall) -> None:
        step_id = self._begin_step(cp, "tool_call", call.tool_name, call.arguments)
        started, t0 = now_iso(), time.monotonic()
        spec = self.registry.get_spec(call.tool_name)
        timeout = spec.timeout_s if spec else 30.0
        ctx = ToolContext(
            case_id=cp.case_id, run_id=cp.run_id, step_id=step_id, case_version=cp.case_version,
            deadline=datetime.now(timezone.utc) + timedelta(seconds=timeout), stage=cp.stage,
        )
        ex = self.registry.execute(call.tool_name, call.arguments, ctx, cp.config.enabled_tools)
        cp.usage.tool_calls += 1
        result = ex.result
        if result.case_version is not None:
            cp.case_version = result.case_version
        _merge_sources(cp.source_refs, result.source_refs)
        self._finish_step(cp, step_id, "tool_call", started, t0, status=result.status, tool_name=call.tool_name,
                          arguments=call.arguments, result_summary=result.summary, source_refs=result.source_refs,
                          error=result.error, attempt=max(ex.attempts, 1))
        cp.messages.append(_tool_message(call.id, result))

    def _handle_builtin(self, cp: Checkpoint, call: ToolCall) -> str | None:
        spec = BUILTINS[call.tool_name]
        try:
            jsonschema.validate(call.arguments, spec.parameters)
        except jsonschema.ValidationError as e:
            step_id = self._begin_step(cp, "error", call.tool_name, call.arguments)
            err = ToolError(code="INVALID_ARGS", message=e.message)
            self._finish_step(cp, step_id, "error", now_iso(), time.monotonic(), status="error",
                              tool_name=call.tool_name, arguments=call.arguments, error=err, result_summary=e.message)
            cp.messages.append(_tool_message(call.id, ToolResult.fail("INVALID_ARGS", e.message)))
            return None

        if call.tool_name == "finish":
            cp.messages.append(_tool_message(call.id, ToolResult(status="ok", summary="运行结束。")))
            self._finish(cp, call.arguments)
            return "finished"

        if call.tool_name == "ask_user":
            cp.pending = {"type": "questions", "questions": call.arguments["questions"]}
            step_type, summary = "ask_user", "；".join(q["text"] for q in call.arguments["questions"])
        else:  # request_route_choice
            cp.pending = {"type": "route_choice", **call.arguments}
            step_type, summary = "route_choice", "请用户选择处理路线"
        step_id = self._begin_step(cp, step_type, call.tool_name, call.arguments)
        self._finish_step(cp, step_id, step_type, now_iso(), time.monotonic(), status="waiting",
                          tool_name=call.tool_name, arguments=call.arguments, result_summary=summary)
        cp.messages.append(_tool_message(call.id, ToolResult(status="ok", summary="已发送给用户，等待回复。")))
        return "waiting"

    # ------------------------------------------------------------ endings

    def _finish(self, cp: Checkpoint, args: dict[str, Any]) -> RunInfo:
        snap = self.cases.get_snapshot(cp.case_id)
        cp.final = FinalOutput(
            answer=args.get("answer", ""),
            output_refs=args.get("output_refs", []),
            source_refs=cp.source_refs,
            open_issues=args.get("open_issues", []),
            case_version=snap.case_version,
        )
        step_id = self._begin_step(cp, "finish", "finish", None)
        self._finish_step(cp, step_id, "finish", now_iso(), time.monotonic(), status="ok",
                          tool_name="finish", result_summary=cp.final.answer[:200])
        return self._end(cp, "completed", "run.completed", final=cp.final.model_dump())

    def _budget_exhausted(self, cp: Checkpoint) -> RunInfo:
        cfg = cp.config
        cp.final = FinalOutput(
            answer="已达到本次运行的调用上限，以下为目前已完成的部分。",
            source_refs=cp.source_refs,
            open_issues=[f"达到预算上限（最多 {cfg.max_steps} 步、{cfg.max_tool_calls} 次工具调用），任务未完成。"],
            case_version=cp.case_version,
        )
        return self._end(cp, "budget_exhausted", "run.budget_exhausted", final=cp.final.model_dump())

    def _end(self, cp: Checkpoint, status: str, event: str, **data: Any) -> RunInfo:
        cp.status, cp.pending = status, None
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
            case_id=cp.case_id, run_id=cp.run_id, step_id=step_id, step_type=step_type, status=status,
            started_at=started, duration_ms=duration, case_version=cp.case_version, **fields,
        ))
        err = fields.get("error")
        self._emit(cp, "step.finished", step_id, {
            "status": status, "summary": fields.get("result_summary", ""), "duration_ms": duration,
            "error": err.model_dump() if err else None,
        })

    def _record(self, cp: Checkpoint, step_type: str, started: str, **fields: Any) -> None:
        step_id = self._begin_step(cp, step_type, fields.get("tool_name"), fields.get("arguments"))
        self._finish_step(cp, step_id, step_type, started, time.monotonic(), **fields)


# ---------------------------------------------------------------- helpers

def _info(cp: Checkpoint) -> RunInfo:
    return RunInfo(run_id=cp.run_id, case_id=cp.case_id, status=cp.status, pending=cp.pending,
                   final=cp.final, usage=cp.usage, error=cp.error)


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


def _reply_text(reply: UserReply, pending: dict[str, Any]) -> str:
    lines: list[str] = []
    questions = {q["id"]: q["text"] for q in pending.get("questions", [])}
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
