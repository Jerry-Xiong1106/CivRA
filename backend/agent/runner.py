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
from backend.agent.tasks import CivRAAgentState, current, record_mediation_answer, select_task
from backend.agent.litigation import ENTRY_TEXT, EVENTS, Feedback, confirmed_feedback, entry_action, route_task
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

    def litigation_progress(self, case_id, info=None, inventory=None):
        guide = getattr(self.cases, "litigation", None)
        if guide is None:
            return None
        return guide.view(self.cases.repository.get_case(case_id), info.tasks if info else (),
                          info.wait if info else None, inventory)

    def enter_litigation(self, case_id, *, conversation_id, run_id=None, history_run_id=None,
                         request_id=None, actor_id="user", switch_wait_id=None, expected_case_version=None):
        if not getattr(self.cases, "litigation", None):
            raise RunStateError("诉讼引导需要案件业务存储。")
        latest = self.store.latest_run(conversation_id) or history_run_id
        previous = self.get_run(latest) if latest else None
        if previous and previous.case_id != case_id:
            raise RunStateError("会话与案件不匹配。")
        snapshot = self.cases.get_snapshot(case_id)
        if expected_case_version is not None and snapshot.case_version != expected_case_version:
            raise VersionConflict("案件版本已更新，请刷新。")
        stage = snapshot.stage
        try:
            action = entry_action(stage, previous, switch_wait_id)
        except ValueError as exc:
            raise RunStateError(str(exc)) from exc
        if action == "reuse":
            return previous
        if action == "resume":
            return self.resume_run(latest, UserReply(route_choice="litigation"), wait_id=previous.wait.wait_id,
                                   request_id=request_id, actor_id=actor_id, conversation_id=conversation_id,
                                   expected_case_version=expected_case_version, litigation_entry=True)
        if action == "switch":
            self.cancel_run(latest)
        return self.start_run(case_id, ENTRY_TEXT, conversation_id=conversation_id,
                              run_id=run_id, history_run_id=latest, request_id=request_id,
                              actor_id=actor_id, litigation_entry=True, expected_case_version=expected_case_version)

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
        litigation_entry: bool = False,
        litigation_feedback: Feedback | None = None,
        expected_case_version: int | None = None,
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
        tasks = []
        latest = self.store.latest_run(conversation_id) if conversation_id else None
        if latest and history_run_id and latest != history_run_id:
            raise RunStateError("会话已有更新的运行。")
        history_run_id = history_run_id or latest
        parent_checkpoint_id = None
        expected_previous = None
        if history_run_id is not None:
            previous = self.store.load_checkpoint(history_run_id)
            if previous.case_id != case_id or previous.status not in TERMINAL_STATUSES:
                raise RunStateError("History must belong to a finished run of the same case")
            if previous.thread_id:
                expected_previous = previous.run_id
                if conversation_id and conversation_id != previous.thread_id:
                    raise RunStateError("History belongs to a different conversation")
                if self.store.latest_run(previous.thread_id) != history_run_id:
                    raise RunStateError("会话已有更新的运行。")
            conversation_id = conversation_id or previous.thread_id or f"conv_{uuid.uuid4().hex}"
            parent_checkpoint_id = self.store.continue_thread(previous, conversation_id)
            tasks = self.store.read_tasks(history_run_id)
        if run_id is not None:
            if not run_id.startswith("run_") or not all(c.isalnum() or c == "_" for c in run_id):
                raise RunStateError("Invalid application run_id")
            if self.store.exists(run_id):
                raise RunStateError("Application run_id already exists")
        snap = self.cases.get_snapshot(case_id)
        if expected_case_version is not None and snap.case_version != expected_case_version:
            raise VersionConflict("案件版本已更新，请刷新。")
        if litigation_feedback:
            if not getattr(self.cases, "litigation", None) or litigation_feedback not in EVENTS:
                raise RunStateError("不支持此办理反馈。")
            self.cases.litigation.validate_feedback(self.cases.repository.get_case(case_id),
                                                   litigation_feedback, user_message)
        cp = Checkpoint(
            run_id=run_id or f"run_{datetime.now():%Y%m%d}_{uuid.uuid4().hex[:6]}",
            thread_id=conversation_id or f"conv_{uuid.uuid4().hex}",
            graph_checkpoint_id=parent_checkpoint_id,
            case_id=case_id,
            engine=ENGINE,
            status="running",
            config=config or self.default_config,
            case_version=snap.case_version,
            stage=snap.stage,
            experiment_run_id=experiment_run_id,
            start_request_id=request_id,
        )
        with self.store.begin_run(cp, expected_previous):
            if litigation_entry and cp.stage != "litigation":
                cp.case_version = self.cases.set_stage(case_id, "litigation")
                cp.stage = "litigation"
            saved_id = self._record_user_message(cp, user_message, f"start:{request_id or cp.run_id}", message_id, actor_id)
            if litigation_feedback:
                self._litigation_feedback(cp, litigation_feedback, saved_id, actor_id, user_message)
            self.store.emit(cp, "run.started", checkpoint=True, case_id=case_id, case_version=cp.case_version,
                            mode=cp.config.mode, experiment_run_id=experiment_run_id)
        if litigation_entry or litigation_feedback:
            cp.conversation_route = "case_work"
            cp.routing_source = "litigation_entry" if litigation_entry else "litigation_feedback"
            route_task(tasks, self.litigation_progress(case_id))
        else:
            intent = self._select_conversation_route(cp, user_message, snap, tasks)
            if cp.conversation_route == 'case_work':
                if intent and snap.stage != "litigation":
                    intent.route_step = None
                elif intent and any(t.route_step for t in tasks):
                    if intent.kind in ("document", "evidence_directory"):
                        intent.route_step = "draft" if intent.kind == "document" else "evidence"
                    elif intent.kind == "text" and intent.action == "new":
                        intent.route_step = None
                select_task(tasks, intent, user_message)
                progress = snap.summary.get("route_progress")
                if progress and not current(tasks) and intent and intent.action == "continue":
                    route_task(tasks, progress)
        return self._drive(cp, [HumanMessage(content=user_message)], tasks=tasks)

    def _select_conversation_route(self, cp, message, snapshot, tasks):
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
                messages = ROUTING_TEMPLATE.invoke({"routing_input": routing_input(message, snapshot, tasks)}).to_messages()
                try:
                    params = {"temperature": 0, "max_tokens": min(cp.config.max_output_tokens or 700, 700)}
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
                    return decision.task
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
        conversation_id: str | None = None,
        litigation_feedback: Feedback | None = None,
        litigation_entry: bool = False,
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
        if not litigation_feedback and (event := confirmed_feedback(wait, reply)):
            litigation_feedback = event
            if reply.text and not reply.answers and len(wait.questions) == 1:
                reply = reply.model_copy(update={"answers": {wait.questions[0].question_id: reply.text}, "text": None})
        reply_text = _reply_text(reply, wait)
        if litigation_feedback:
            self.cases.litigation.validate_feedback(self.cases.repository.get_case(cp.case_id),
                                                   litigation_feedback, reply_text)
        if conversation_id and cp.thread_id not in (None, conversation_id):
            raise RunStateError("Run belongs to a different conversation")
        if cp.thread_id is None:
            thread = conversation_id or f"conv_{uuid.uuid4().hex}"
            cp.graph_checkpoint_id = self.store.continue_thread(cp, thread)
            cp.thread_id = thread
            self.store.save_checkpoint(cp)
        saved_id = self._record_user_message(cp, reply_text, f"resume:{run_id}:{wait_id}", message_id, actor_id)
        if litigation_feedback:
            self._litigation_feedback(cp, litigation_feedback, saved_id, actor_id, reply.text or reply_text)
        if request_id is not None:
            cp.applied_requests.append(request_id)

        if wait.kind == "route_selection":
            args = {"route_choice": reply.route_choice}
            with self.store.step(cp, "route_choice", "request_route_choice", args) as step:
                cp.case_version = self.cases.set_stage(cp.case_id, reply.route_choice)
                cp.stage = reply.route_choice
                cp.route_selected = True
                cp.conversation_route, cp.routing_source = "case_work", "user_route_selection"
                if litigation_entry and reply.route_choice == "litigation":
                    cp.routing_source = "litigation_entry"
                step.result_summary = f"用户选择路线：{reply.route_choice}"

        new_message = HumanMessage(content=reply_text)
        cp.status, cp.wait = "running", None
        # New information from the user opens a fresh retrieve/verify budget.
        cp.feedback_rounds, cp.limit_notes = {}, []
        self.store.emit(cp, "run.resumed", wait_id=wait_id, request_id=request_id,
                        reply=reply.model_dump(exclude_defaults=True))
        return self._drive(cp, [new_message], resume=True, reply=reply, wait=wait, feedback=litigation_feedback)

    def _record_user_message(self, cp, text, key, message_id, actor_id):
        """Persist trusted caller input before the model can propose confirmations.

        Applications that already saved the input pass their message_id. The
        factory enables this hook for CLI/evaluation callers using CaseService.
        A deterministic ID also makes a retry after a partial save safe.
        """
        record = getattr(self.cases, "record_user_reply", None)
        if not self.record_user_messages or message_id is not None or record is None:
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
        result = record(case_id=cp.case_id, expected_case_version=snapshot.case_version,
                        request_id=identifier, message_id=identifier, actor_id=actor_id,
                        text=text, created_at=datetime.now(timezone.utc))
        if result.status != "ok":
            raise RunStateError("用户消息保存失败，请刷新案件版本后重试。")
        cp.case_version = result.case_version
        return identifier

    def _litigation_feedback(self, cp, event, statement_id, actor_id, text):
        if event not in EVENTS or not statement_id or not getattr(self.cases, "litigation", None):
            raise RunStateError("需要当前案件中已保存的用户办理反馈。")
        self.cases.litigation.record(self.cases.repository.get_case(cp.case_id), event,
                                     statement_id, actor_id, text, statement_id)

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

    def get_run(self, run_id: str) -> RunInfo:
        cp = self.store.load_checkpoint(run_id)
        info = cp.info()
        info.tasks = self.store.read_tasks(run_id)
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

    def _drive(self, cp: Checkpoint, new_messages: list[BaseMessage], *, resume=False, tasks=None, reply=None, wait=None, feedback=None) -> RunInfo:
        middleware = RunMiddleware(cp, self.llm, self.tools, self.cases, self.store)
        middleware.tasks = self.store.read_tasks(cp.run_id) if resume else (tasks or [])
        if feedback:
            cp.routing_source = "litigation_feedback"
            route_task(middleware.tasks, self.litigation_progress(cp.case_id))
        elif wait and any(q.field == "litigation_role" for q in wait.questions):
            progress = self.litigation_progress(cp.case_id)
            if progress and progress["role"] == "defendant":
                route_task(middleware.tasks, progress)
        middleware.allow_task_change = bool(reply and not reply.route_choice) or cp.routing_source in ("config", "litigation_entry", "litigation_feedback")
        if reply:
            task = current(middleware.tasks)
            if task and task.status == "waiting_input":
                task.status = "active"
            if task and task.kind == "mediation_plan" and wait:
                for question in wait.questions:
                    if answer := reply.answers.get(question.question_id):
                        record_mediation_answer(task, question.field, answer)
            middleware.reply_evidence = list(reply.answers.values()) + ([reply.text] if reply.text else [])
            middleware.reply_files = reply.file_ids
        if reply and reply.route_choice == "litigation" and getattr(self.cases, "litigation", None):
            task = current(middleware.tasks)
            if task and task.kind in ("document", "unsupported_document"):
                task.route_step = "draft"
            elif not task or cp.routing_source == "litigation_entry":
                route_task(middleware.tasks, self.litigation_progress(cp.case_id))
        if resume and cp.route_selected and not middleware.tasks:
            # A consultation may be promoted on a user's route choice. The choice
            # alone supplies no deliverable; retain the original user's request.
            history = self.store.read_messages(cp.run_id)
            latest = next((m.text for m in reversed(history) if isinstance(m, HumanMessage)), "")
            if latest:
                select_task(middleware.tasks, None, latest)
        t0 = middleware.started_at
        tools = [*self.tools, *BUILTINS.values()]
        config = {"configurable": {"thread_id": cp.thread_id}, "max_concurrency": 1,
                  "recursion_limit": max(100, cp.config.max_steps * 8)}
        try:
            with self.store.graph_checkpointer() as checkpointer:
                agent = create_agent(self.llm, tools=tools, middleware=[middleware],
                                     checkpointer=checkpointer, state_schema=CivRAAgentState, name="CivRA")
                # A resumed logical run is another turn in the same durable thread.
                if resume or cp.graph_checkpoint_id:
                    head = checkpointer.get_tuple(config)
                    if head is None or (cp.graph_checkpoint_id and head.checkpoint["id"] != cp.graph_checkpoint_id):
                        raise RunStateError("会话检查点缺失或已经变化。")
                agent.invoke({"messages": new_messages, "tasks": middleware.task_rows()}, config=config)
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
