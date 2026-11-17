"""Lifecycle and project policies around LangChain create_agent (contract §4–§7).

start_run and resume_run are synchronous: they drive the run until it waits for
the user or ends. The API layer can call them in a worker thread and stream
progress with iter_events.
"""

from __future__ import annotations

import hashlib
import json
import time
import uuid
from datetime import datetime, timezone
from typing import Any, Iterator

from langchain.agents import create_agent
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import BaseMessage, HumanMessage

from backend.agent.runtime.builtins import BUILTINS
from backend.agent.case_port import CaseStatePort
from backend.agent.model.llm import latest_answer, response_from_message
from backend.agent.routing import RouteDecision, routing_input
from backend.agent.runtime.middleware import RunMiddleware
from backend.agent.model.prompts import PROMPT_SPEC, ROUTING_PROMPT, ROUTING_TEMPLATE
from backend.agent.runtime.state import CURRENT_ENGINE as ENGINE, Checkpoint
from backend.agent.runtime.store import RunStore
from backend.common.schemas import (
    TERMINAL_STATUSES, RunConfig, RunEvent, RunInfo, RuntimeManifest,
    ToolManifestEntry, TraceExport, TraceStep, UserReply, Wait, ToolError,
)
from langchain_core.tools import BaseTool
from backend.tools.policy import describe_tool


RUNTIME_VERSION = "1.5.0"


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
        llm: BaseChatModel,
        tools: list[BaseTool],
        cases: CaseStatePort,
        store: RunStore | None = None,
        default_config: RunConfig | None = None,
        *,
        record_user_messages: bool = False,
    ) -> None:
        if getattr(llm, "max_retries", 0) not in (0, None):
            raise ValueError("Model max_retries must be 0; retries must be accounted by the caller")
        self.llm = llm
        self.tools = list(tools)
        names = [t.name for t in self.tools]
        if len(set(names)) != len(names) or set(names) & BUILTINS.keys():
            raise ValueError("duplicate or reserved tool name")
        self.cases = cases
        self.store = store or RunStore()
        self.default_config = default_config or RunConfig()
        self.record_user_messages = record_user_messages

    # ------------------------------------------------------------ public API

    def start_run(
        self,
        case_id: str,
        user_message: str,
        *,
        config: RunConfig | None = None,
        experiment_run_id: str | None = None,
        request_id: str | None = None,
        message_id: str | None = None,
        actor_id: str = "user",
        run_id: str | None = None,
        history_run_id: str | None = None,
    ) -> RunInfo:
        """Start a run; optionally carry a completed same-case conversation forward.

        run_id/history_run_id are trusted application inputs, never model arguments.
        Repeating a request_id returns its existing run.
        """
        if request_id is not None:
            for existing in self.store.list_checkpoints():
                if existing.start_request_id == request_id:
                    return self.get_run(existing.run_id)
        self._ensure_not_running(case_id)
        history = []
        if history_run_id is not None:
            previous = self.store.load_checkpoint(history_run_id)
            if previous.case_id != case_id or previous.status not in TERMINAL_STATUSES:
                raise RunStateError("History must belong to a finished run of the same case")
            history = self.store.read_messages(history_run_id)
        if run_id is not None:
            if not run_id.startswith("run_") or not all(c.isalnum() or c == "_" for c in run_id):
                raise RunStateError("Invalid application run_id")
            if self.store.exists(run_id):
                raise RunStateError("Application run_id already exists")
        snap = self.cases.get_snapshot(case_id)
        cp = Checkpoint(
            run_id=run_id or f"run_{datetime.now():%Y%m%d}_{uuid.uuid4().hex[:6]}",
            case_id=case_id,
            engine=ENGINE,
            status="running",
            config=config or self.default_config,
            case_version=snap.case_version,
            stage=snap.stage,
            experiment_run_id=experiment_run_id,
            start_request_id=request_id,
        )
        self._record_user_message(cp, user_message, f"start:{request_id or cp.run_id}", message_id, actor_id)
        self.store.emit(cp, "run.started", case_id=case_id, case_version=cp.case_version,
                        mode=cp.config.mode, experiment_run_id=experiment_run_id)
        self.store.save_checkpoint(cp)
        self._select_conversation_route(cp, user_message, snap)
        return self._drive(cp, [*history, HumanMessage(content=user_message)])

    def _select_conversation_route(self, cp, message, snapshot):
        requested = cp.config.conversation_route
        if requested != "auto":
            cp.conversation_route, cp.routing_source = requested, "config"
            self.store.save_checkpoint(cp)
            return
        if cp.budget_reason(0):
            return  # _drive reports the exhausted budget without a model call.
        started = time.monotonic()
        try:
            with self.store.step(cp, "model_call", arguments={"purpose": "conversation_routing"}) as step:
                cp.usage.model_calls += 1
                messages = ROUTING_TEMPLATE.invoke({"routing_input": routing_input(message, snapshot)}).to_messages()
                try:
                    params = {"temperature": 0, "max_tokens": min(cp.config.max_output_tokens or 300, 300)}
                    if cp.config.model:
                        params["model"] = cp.config.model
                    reply = self.llm.bind(**params).invoke(messages)
                    response = response_from_message(reply, self.llm, round((time.monotonic()-started)*1000))
                    self.store.add_model_usage(cp, response)
                    step.token_usage, step.cost = response.usage, response.cost
                    step.result_ref = self.store.save_result(cp.run_id, step.step_id, {
                        "purpose": "conversation_routing", "request": {"system": ROUTING_PROMPT, "input": messages[1].content},
                        "response": response.model_dump(mode="json"),
                    })
                    if reply.tool_calls or reply.invalid_tool_calls:
                        raise ValueError("routing must not call tools")
                    decision = RouteDecision.model_validate_json(reply.text)
                    cp.conversation_route, cp.routing_reason = decision.route, decision.reason
                    cp.routing_source = "model"
                    step.result_summary = f"对话入口：{decision.route}；{decision.reason}"
                except Exception as exc:
                    # Invalid decisions never grant write-capable business tools.
                    cp.conversation_route, cp.routing_source = "consultation", "fallback"
                    cp.routing_reason = "入口识别失败，先提供咨询，不自动执行案件操作。"
                    step.status = "error"
                    step.error = ToolError(code="UPSTREAM", message=f"入口识别失败（{type(exc).__name__}），使用咨询入口")
                    step.result_summary = step.error.message
                    if step.token_usage is None:
                        cp.usage.tokens_complete, cp.usage.estimated_cost = False, None
        finally:
            cp.usage.duration_ms += round((time.monotonic()-started)*1000)
            self.store.save_checkpoint(cp)

    def resume_run(
        self,
        run_id: str,
        reply: UserReply,
        *,
        wait_id: str,
        expected_case_version: int | None = None,
        request_id: str | None = None,
        message_id: str | None = None,
        actor_id: str = "user",
    ) -> RunInfo:
        """Answer the run's current wait and continue.

        Raises StaleWait / VersionConflict without executing anything. Repeating a
        request_id that was already applied returns the current state instead of
        running again, so a client can safely retry after a timeout.
        """
        try:
            cp = self.store.load_checkpoint(run_id)
        except ValueError:
            raise RunStateError("运行记录格式已变化或损坏，请新建会话。") from None
        if request_id is not None and request_id in cp.applied_requests:
            return self.get_run(run_id)
        if cp.status != "waiting_user" or cp.wait is None:
            raise RunStateError(f"run {run_id} is {cp.status}, not waiting_user")
        if wait_id != cp.wait.wait_id:
            raise StaleWait(f"wait_id {wait_id} is stale; current wait is {cp.wait.wait_id}")
        current_version = self.cases.get_snapshot(cp.case_id).case_version
        if expected_case_version is not None and expected_case_version != current_version:
            raise VersionConflict(f"expected case_version {expected_case_version}, case is at {current_version}")
        cp.case_version = current_version
        self._ensure_not_running(cp.case_id)
        wait = cp.wait

        if wait.kind == "route_selection":
            allowed = [o.route for o in wait.options]
            if reply.route_choice not in allowed:
                raise RunStateError(f"route_choice must be one of {allowed}")
        reply_text = _reply_text(reply, wait)
        self._record_user_message(cp, reply_text, f"resume:{run_id}:{wait_id}", message_id, actor_id)
        if request_id is not None:
            cp.applied_requests.append(request_id)

        if wait.kind == "route_selection":
            args = {"route_choice": reply.route_choice}
            with self.store.step(cp, "route_choice", "request_route_choice", args) as step:
                cp.case_version = self.cases.set_stage(cp.case_id, reply.route_choice)
                cp.stage = reply.route_choice
                cp.route_selected = True
                cp.conversation_route, cp.routing_source = "case_work", "user_route_selection"
                step.result_summary = f"用户选择路线：{reply.route_choice}"

        new_message = HumanMessage(content=reply_text)
        cp.status, cp.wait = "running", None
        # New information from the user opens a fresh retrieve/verify budget.
        cp.feedback_rounds, cp.limit_notes = {}, []
        self.store.emit(cp, "run.resumed", wait_id=wait_id, request_id=request_id,
                        reply=reply.model_dump(exclude_defaults=True))
        return self._drive(cp, [new_message], resume=True)

    def _record_user_message(self, cp, text, key, message_id, actor_id):
        """Persist trusted caller input before the model can propose confirmations.

        Applications that already saved the input pass their message_id. The
        factory enables this hook for CLI/evaluation callers using CaseService.
        A deterministic ID also makes a retry after a partial save safe.
        """
        record = getattr(self.cases, "record_user_reply", None)
        if not self.record_user_messages or message_id is not None or record is None:
            return
        snapshot = self.cases.get_snapshot(cp.case_id)
        identifier = "runtime_msg_" + hashlib.sha256(key.encode("utf-8")).hexdigest()[:32]
        existing = next((item for item in snapshot.summary.get("statements", [])
                         if item["message_id"] == identifier), None)
        if existing is not None:
            if existing["text"] != text or existing["actor_id"] != actor_id:
                raise RunStateError("同一用户消息标识已保存不同内容。")
            cp.case_version = snapshot.case_version
            return
        result = record(case_id=cp.case_id, expected_case_version=snapshot.case_version,
                        request_id=identifier, message_id=identifier, actor_id=actor_id,
                        text=text, created_at=datetime.now(timezone.utc))
        if result.status != "ok":
            raise RunStateError("用户消息保存失败，请刷新案件版本后重试。")
        cp.case_version = result.case_version

    def cancel_run(self, run_id: str, *, request_id: str | None = None) -> RunInfo:
        cp = self.store.load_checkpoint(run_id)
        if cp.status in TERMINAL_STATUSES:
            return self.get_run(run_id)
        if cp.status == "waiting_user":
            cp.stop("cancelled", "用户取消", latest_answer(self.store.read_messages(run_id)))
            return self.store.end(cp, reason="用户取消")
        # running: middleware observes cancellation at the next execution boundary
        self.store.request_cancel(run_id)
        return self.get_run(run_id)

    def get_run(self, run_id: str) -> RunInfo:
        cp = self.store.load_checkpoint(run_id)
        info = cp.info()
        if cp.final is not None:
            info.stale = self.cases.get_snapshot(cp.case_id).case_version > cp.final.case_version
        return info

    def iter_events(self, run_id: str, after_seq: int = 0) -> Iterator[RunEvent]:
        yield from self.store.read_events(run_id, after_seq)

    def get_trace(self, run_id: str) -> list[TraceStep]:
        return self.store.read_trace(run_id)

    def export_trace(self, run_id: str, cursor: int = 0, limit: int = 100) -> TraceExport:
        """Page through trace steps in step order. cursor = number of steps already read."""
        if cursor < 0 or limit < 1:
            raise ValueError("cursor must be nonnegative and limit positive")
        cp = self.store.load_checkpoint(run_id)
        steps = self.store.read_trace(run_id, cursor=cursor, limit=limit + 1)
        page = steps[:limit]
        end = cursor + len(page)
        more_stored = len(steps) > limit
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
        specs = sorted([describe_tool(t) for t in [*self.tools, *BUILTINS.values()]], key=lambda s: s.name)
        entries = [
            ToolManifestEntry(name=s.name, owner=s.owner, stages=s.stages, schema_sha256=_sha256(s.model_dump()))
            for s in specs
        ]
        return RuntimeManifest(
            runtime_version=RUNTIME_VERSION,
            prompt_sha256=_sha256(PROMPT_SPEC),
            tool_schema_sha256=_sha256([e.model_dump() for e in entries]),
            tools=entries,
            model={"client": type(self.llm).__name__, "default_model": getattr(self.llm, "model_name", None)},
            default_config=self.default_config,
        )

    # ------------------------------------------------------------ framework execution

    def _drive(self, cp: Checkpoint, new_messages: list[BaseMessage], *, resume=False) -> RunInfo:
        middleware = RunMiddleware(cp, self.llm, self.tools, self.cases, self.store)
        t0 = middleware.started_at
        tools = [*self.tools, *BUILTINS.values()]
        config = {"configurable": {"thread_id": cp.run_id}, "max_concurrency": 1,
                  "recursion_limit": max(100, cp.config.max_steps * 8)}
        try:
            with self.store.graph_checkpointer() as checkpointer:
                agent = create_agent(self.llm, tools=tools, middleware=[middleware],
                                     checkpointer=checkpointer, name="CivRA")
                # A resumed logical run is another turn in the same durable thread.
                if resume and not checkpointer.get_tuple(config):
                    raise RunStateError("框架检查点缺失，请重新开始运行。")
                agent.invoke({"messages": new_messages}, config=config)
        except Exception as exc:
            if cp.status not in TERMINAL_STATUSES:
                middleware.stop("failed", f"执行失败（{type(exc).__name__}）")
        finally:
            middleware.executor.close(wait=False)
            cp.usage.duration_ms += int((time.monotonic() - t0) * 1000)
            if cp.status in TERMINAL_STATUSES:
                data = {"reason": middleware.stop_reason} if middleware.stop_reason is not None else {}
                self.store.end(cp, **data)
            else:
                self.store.save_checkpoint(cp)
        return cp.info()

    # ------------------------------------------------------------ helpers

    def _ensure_not_running(self, case_id: str) -> None:
        for other in self.store.list_checkpoints():
            if other.case_id == case_id and other.status == "running" and other.engine == ENGINE:
                raise RunConflict(f"case {case_id} already has a running run: {other.run_id}")


def _sha256(obj: Any) -> str:
    text = obj if isinstance(obj, str) else json.dumps(obj, ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(text.encode()).hexdigest()


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
