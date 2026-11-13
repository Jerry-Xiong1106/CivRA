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
from datetime import datetime
from typing import Any, Iterator

from langchain.agents import create_agent
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import BaseMessage, HumanMessage

from backend.agent.runtime.builtins import BUILTINS
from backend.agent.case_port import CaseStatePort
from backend.agent.model.llm import latest_answer
from backend.agent.runtime.middleware import RunMiddleware
from backend.agent.model.prompts import SYSTEM_PROMPT
from backend.agent.runtime.state import CURRENT_ENGINE as ENGINE, Checkpoint
from backend.agent.runtime.store import RunStore
from backend.common.schemas import (
    TERMINAL_STATUSES, RunConfig, RunEvent, RunInfo, RuntimeManifest,
    ToolManifestEntry, TraceExport, TraceStep, UserReply, Wait,
)
from backend.tools.registry import ToolRegistry


RUNTIME_VERSION = "1.3.0"


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
        registry: ToolRegistry,
        cases: CaseStatePort,
        store: RunStore | None = None,
        default_config: RunConfig | None = None,
    ) -> None:
        if getattr(llm, "max_retries", 0) not in (0, None):
            raise ValueError("Model max_retries must be 0; retries must be accounted by the caller")
        self.llm = llm
        self.registry = registry
        self.cases = cases
        self.store = store or RunStore()
        self.default_config = default_config or RunConfig()

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
            engine=ENGINE,
            status="running",
            config=config or self.default_config,
            case_version=snap.case_version,
            stage=snap.stage,
            experiment_run_id=experiment_run_id,
            start_request_id=request_id,
        )
        self.store.emit(cp, "run.started", case_id=case_id, case_version=snap.case_version,
                        mode=cp.config.mode, experiment_run_id=experiment_run_id)
        self.store.save_checkpoint(cp)
        return self._drive(cp, [HumanMessage(content=user_message)])

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
        self._ensure_not_running(cp.case_id)
        wait = cp.wait

        if wait.kind == "route_selection":
            allowed = [o.route for o in wait.options]
            if reply.route_choice not in allowed:
                raise RunStateError(f"route_choice must be one of {allowed}")
        if request_id is not None:
            cp.applied_requests.append(request_id)

        if wait.kind == "route_selection":
            args = {"route_choice": reply.route_choice}
            with self.store.step(cp, "route_choice", "request_route_choice", args) as step:
                cp.case_version = self.cases.set_stage(cp.case_id, reply.route_choice)
                cp.stage = reply.route_choice
                step.result_summary = f"用户选择路线：{reply.route_choice}"

        new_message = HumanMessage(content=_reply_text(reply, wait))
        cp.status, cp.wait = "running", None
        # New information from the user opens a fresh retrieve/verify budget.
        cp.feedback_rounds, cp.limit_notes = {}, []
        self.store.emit(cp, "run.resumed", wait_id=wait_id, request_id=request_id,
                        reply=reply.model_dump(exclude_defaults=True))
        return self._drive(cp, [new_message], resume=True)

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
            model={"client": type(self.llm).__name__, "default_model": getattr(self.llm, "model_name", None)},
            default_config=self.default_config,
        )

    # ------------------------------------------------------------ framework execution

    def _drive(self, cp: Checkpoint, new_messages: list[BaseMessage], *, resume=False) -> RunInfo:
        middleware = RunMiddleware(cp, self.llm, self.registry, self.cases, self.store)
        t0 = middleware.started_at
        specs = [*self.registry.all_specs(), *BUILTINS.values()]
        tools = [middleware.framework_tool(spec) for spec in specs]
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
