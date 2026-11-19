"""Run lifecycle around LangChain create_agent.

A conversation is a chain of runs in one LangGraph thread. start_run handles a
new user message, select_route switches the case to the route the user picked in
the interface and starts guiding its first open step, and resume_run answers a
waiting run. Answering a step_confirmation wait with "done" records the step as
completed (quoting the user's saved reply) and the Agent continues with the next
step in the same run. All entry points are synchronous; the API runs them in a
worker thread and streams progress with iter_events.
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
from langchain_core.tools import BaseTool

from backend.agent.case_port import CaseStatePort
from backend.agent.model.llm import latest_answer
from backend.agent.model.prompts import PROMPT_SPEC, ROUTE_NAMES
from backend.agent.runtime.builtins import BUILTINS
from backend.agent.runtime.middleware import RunMiddleware
from backend.agent.runtime.state import CURRENT_ENGINE as ENGINE, Checkpoint
from backend.agent.runtime.store import RunStore
from backend.common.schemas import (
    TERMINAL_STATUSES, RunConfig, RunEvent, RunInfo, RuntimeManifest,
    ToolManifestEntry, TraceExport, TraceStep, UserReply, Wait,
)
from backend.tools.policy import describe_tool

RUNTIME_VERSION = "2.0.0"


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

    # ------------------------------------------------------------ starting runs

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
        conversation_id: str | None = None,
        expected_case_version: int | None = None,
    ) -> RunInfo:
        """Start a run for a new user message, continuing the conversation's history.

        run_id/history_run_id are trusted application inputs, never model arguments.
        Repeating a request_id returns its existing run.
        """
        if request_id is not None and (existing := self.store.run_for_start_request(request_id)):
            return self.get_run(existing)
        cp, expected_previous = self._new_checkpoint(
            case_id, config=config, experiment_run_id=experiment_run_id, request_id=request_id, run_id=run_id,
            history_run_id=history_run_id, conversation_id=conversation_id,
            expected_case_version=expected_case_version)
        with self.store.begin_run(cp, expected_previous):
            self._record_user_message(cp, user_message, f"start:{request_id or cp.run_id}", message_id, actor_id)
            self.store.emit(cp, "run.started", checkpoint=True, case_id=case_id, case_version=cp.case_version,
                            mode=cp.config.mode, experiment_run_id=experiment_run_id)
        return self._drive(cp, [HumanMessage(content=user_message)])

    def select_route(
        self,
        case_id: str,
        route: str,
        *,
        conversation_id: str | None = None,
        run_id: str | None = None,
        history_run_id: str | None = None,
        request_id: str | None = None,
        actor_id: str = "user",
        expected_case_version: int | None = None,
        config: RunConfig | None = None,
    ) -> RunInfo:
        """The user picked a route in the interface: save it and start guiding its current step.

        A run still waiting for the user is cancelled first; switching routes keeps
        each route's progress, so returning to a route resumes where it stopped.
        """
        if route not in ROUTE_NAMES:
            raise RunStateError(f"unknown route: {route}")
        if request_id is not None and (existing := self.store.run_for_start_request(request_id)):
            return self.get_run(existing)
        latest = self.store.latest_run(conversation_id) if conversation_id else history_run_id
        if latest:
            previous = self.store.load_checkpoint(latest)
            if previous.status == "running":
                raise RunConflict("当前仍在处理，请稍后再切换路线。")
            if previous.status == "waiting_user":
                self.cancel_run(latest)
        snapshot = self.cases.get_snapshot(case_id)
        if expected_case_version is not None and snapshot.case_version != expected_case_version:
            raise VersionConflict("案件版本已更新，请刷新。")
        message = f"我选择「{ROUTE_NAMES[route]}」，请按步骤引导我继续。"
        cp, expected_previous = self._new_checkpoint(
            case_id, config=config, request_id=request_id, run_id=run_id,
            history_run_id=latest, conversation_id=conversation_id)
        with self.store.begin_run(cp, expected_previous):
            if snapshot.stage != route:
                with self.store.step(cp, "route_selection", arguments={"route": route}) as step:
                    cp.case_version = self.cases.select_route(case_id, route)
                    cp.stage = route
                    step.result_summary = f"用户选择路线：{route}"
            self._record_user_message(cp, message, f"route:{request_id or cp.run_id}", None, actor_id)
            self.store.emit(cp, "run.started", checkpoint=True, case_id=case_id, case_version=cp.case_version,
                            mode=cp.config.mode, route=route)
        return self._drive(cp, [HumanMessage(content=message)])

    def _new_checkpoint(self, case_id, *, config=None, experiment_run_id=None, request_id=None, run_id=None,
                        history_run_id=None, conversation_id=None, expected_case_version=None):
        self._ensure_not_running(case_id)
        latest = self.store.latest_run(conversation_id) if conversation_id else None
        if latest and history_run_id and latest != history_run_id:
            raise RunStateError("会话已有更新的运行。")
        history_run_id = history_run_id or latest
        parent_checkpoint_id = expected_previous = None
        inherited_sources = []
        if history_run_id is not None:
            previous = self.store.load_checkpoint(history_run_id)
            if previous.case_id != case_id or previous.status not in TERMINAL_STATUSES:
                raise RunStateError("History must belong to a finished run of the same case")
            if conversation_id and conversation_id != previous.thread_id:
                raise RunStateError("History belongs to a different conversation")
            if self.store.latest_run(previous.thread_id) != history_run_id:
                raise RunStateError("会话已有更新的运行。")
            expected_previous = previous.run_id
            conversation_id = previous.thread_id
            parent_checkpoint_id = self.store.continue_thread(previous, conversation_id)
            # Sources retrieved earlier in the conversation stay citable in later runs.
            inherited_sources = previous.source_refs
        if run_id is not None:
            if not run_id.startswith("run_") or not all(c.isalnum() or c == "_" for c in run_id):
                raise RunStateError("Invalid application run_id")
            if self.store.exists(run_id):
                raise RunStateError("Application run_id already exists")
        snap = self.cases.get_snapshot(case_id)
        if expected_case_version is not None and snap.case_version != expected_case_version:
            raise VersionConflict("案件版本已更新，请刷新。")
        cp = Checkpoint(
            run_id=run_id or f"run_{datetime.now():%Y%m%d}_{uuid.uuid4().hex[:6]}",
            thread_id=conversation_id or f"conv_{uuid.uuid4().hex}",
            graph_checkpoint_id=parent_checkpoint_id, case_id=case_id, engine=ENGINE, status="running",
            config=config or self.default_config, case_version=snap.case_version, stage=snap.stage,
            experiment_run_id=experiment_run_id, start_request_id=request_id, source_refs=inherited_sources,
        )
        return cp, expected_previous

    # ------------------------------------------------------------ answering a wait

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
        conversation_id: str | None = None,
    ) -> RunInfo:
        """Answer the run's current wait and continue.

        Raises StaleWait / VersionConflict without executing anything. Repeating a
        request_id that was already applied returns the current state, so a client
        can safely retry after a timeout.
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
        if conversation_id and cp.thread_id != conversation_id:
            raise RunStateError("Run belongs to a different conversation")
        wait = cp.wait
        if (wait.kind == "step_confirmation") != (reply.step_decision is not None):
            raise RunStateError("步骤确认需要 step_decision（done 或 revise），其他追问不接受该字段。")
        current_version = self.cases.get_snapshot(cp.case_id).case_version
        if expected_case_version is not None and expected_case_version != current_version:
            raise VersionConflict(f"expected case_version {expected_case_version}, case is at {current_version}")
        cp.case_version = current_version
        self._ensure_not_running(cp.case_id)

        reply_text = _reply_text(reply, wait)
        saved_id = self._record_user_message(cp, reply_text, f"resume:{run_id}:{wait_id}", message_id, actor_id)
        if reply.step_decision == "done":
            with self.store.step(cp, "step_completed", arguments={"step_id": wait.step_id}) as step:
                self.cases.complete_step(cp.case_id, wait.step_id, message_id=saved_id, actor_id=actor_id,
                                         quote=reply_text, key=f"step:{run_id}:{wait_id}")
                cp.case_version = self.cases.get_snapshot(cp.case_id).case_version
                step.result_summary = f"用户确认完成步骤：{wait.step_id}"
            # A new step may need its own questions and searches.
            cp.clarification_rounds, cp.search_calls, cp.asked_questions = 0, 0, []
        cp.start_reply_budget()
        if request_id is not None:
            cp.applied_requests.append(request_id)
        cp.status, cp.wait = "running", None
        self.store.emit(cp, "run.resumed", wait_id=wait_id, request_id=request_id,
                        reply=reply.model_dump(exclude_defaults=True))
        return self._drive(cp, [HumanMessage(content=reply_text)], resume=True)

    def _record_user_message(self, cp, text, key, message_id, actor_id):
        """Persist trusted caller input before the model can quote it in records.

        Applications that already saved the input pass their message_id. A
        deterministic ID makes a retry after a partial save safe.
        """
        if not self.record_user_messages or message_id is not None:
            return message_id
        snapshot = self.cases.get_snapshot(cp.case_id)
        identifier = "runtime_msg_" + hashlib.sha256(key.encode("utf-8")).hexdigest()[:32]
        existing = next((item for item in snapshot.summary.get("statements", [])
                         if item["message_id"] == identifier), None)
        if existing is not None:
            if existing["text"] != text or existing["actor_id"] != actor_id:
                raise RunStateError("同一用户消息标识已保存不同内容。")
            cp.case_version = snapshot.case_version
            return identifier
        result = self.cases.record_user_reply(case_id=cp.case_id, expected_case_version=snapshot.case_version,
                                              request_id=identifier, message_id=identifier, actor_id=actor_id,
                                              text=text, created_at=datetime.now(timezone.utc))
        if result.status != "ok":
            raise RunStateError("用户消息保存失败，请刷新案件版本后重试。")
        cp.case_version = result.case_version
        return identifier

    # ------------------------------------------------------------ control and queries

    def cancel_run(self, run_id: str, *, request_id: str | None = None) -> RunInfo:
        cp = self.store.load_checkpoint(run_id)
        if cp.status in TERMINAL_STATUSES:
            return self.get_run(run_id)
        if cp.status == "waiting_user":
            cp.stop("cancelled", "用户取消", latest_answer(self.store.read_messages(run_id)))
            self.store.end(cp, reason="用户取消")
            return self.get_run(run_id)
        # running: middleware observes cancellation at the next execution boundary
        self.store.request_cancel(run_id)
        return self.get_run(run_id)

    def has_run(self, run_id: str) -> bool:
        return self.store.exists(run_id)

    def fail_if_running(self, run_id: str, reason: str) -> None:
        """Mark a run failed when its executor died (server restart, crashed worker)."""
        if not self.store.exists(run_id):
            return
        cp = self.store.load_checkpoint(run_id)
        if cp.status == "running":
            cp.stop("failed", reason)
            self.store.end(cp)

    def route_progress(self, case_id: str) -> dict | None:
        return self.cases.route_progress(case_id)

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
            run_id=run_id, steps=page,
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
        config = {"configurable": {"thread_id": cp.thread_id}, "max_concurrency": 1,
                  "recursion_limit": max(100, cp.config.max_steps * 8)}
        try:
            with self.store.graph_checkpointer() as checkpointer:
                agent = create_agent(self.llm, tools=[*self.tools, *BUILTINS.values()], middleware=[middleware],
                                     checkpointer=checkpointer, name="CivRA")
                # A resumed or continued run is another turn in the same durable thread.
                if resume or cp.graph_checkpoint_id:
                    head = checkpointer.get_tuple(config)
                    if head is None or (cp.graph_checkpoint_id and head.checkpoint["id"] != cp.graph_checkpoint_id):
                        raise RunStateError("会话检查点缺失或已经变化。")
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
                self.store.pin_graph(cp)
                self.store.save_checkpoint(cp)
        return self.get_run(cp.run_id)

    def _ensure_not_running(self, case_id: str) -> None:
        if other := self.store.running_run(case_id):
            raise RunConflict(f"case {case_id} already has a running run: {other}")


def _sha256(obj: Any) -> str:
    text = obj if isinstance(obj, str) else json.dumps(obj, ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(text.encode()).hexdigest()


def _reply_text(reply: UserReply, wait: Wait) -> str:
    lines: list[str] = []
    if reply.step_decision == "done":
        lines.append(f"确认：这一步已完成。（{wait.summary}）" if wait.summary else "确认：这一步已完成。")
    elif reply.step_decision == "revise":
        lines.append("这一步还需要修改。")
    questions = {q.question_id: q.text for q in wait.questions}
    for qid, answer in reply.answers.items():
        lines.append(f"问：{questions.get(qid, qid)}\n答：{answer}")
    if reply.file_ids:
        lines.append(f"用户补充上传了材料：{', '.join(reply.file_ids)}")
    if reply.text:
        lines.append(reply.text)
    return "\n".join(lines) or "（用户未提供内容）"
