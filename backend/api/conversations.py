"""Single-process conversation coordination over the existing case and Agent services.

SQLite receipts make request retries idempotent. Model execution runs outside HTTP
threads. Interrupted work is marked failed at startup and is never blindly replayed.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from threading import BoundedSemaphore, RLock
from uuid import uuid4

from backend.agent.runner import StaleWait, VersionConflict
from backend.agent.litigation import entry_action
from backend.case.models import CaseSnapshot
from backend.common.schemas import RunInfo
from .schemas import MessageReceipt, MessageRequest, RouteSubmission

log = logging.getLogger(__name__)


class Conflict(ValueError):
    pass


class CapacityError(RuntimeError):
    pass


class Conversations:
    def __init__(self, runner, case_service, *, pending_limit=16):
        self.runner, self.service = runner, case_service
        self.repository = case_service.repository
        self._lock = RLock()
        self._capacity = BoundedSemaphore(pending_limit)
        # Only one coordinator may own this database. Recovery must not mark
        # another live process's work interrupted (including --workers > 1).
        self._lease = self.repository.path.resolve().with_suffix(".api.lock").open("a+b")
        if self._lease.tell() == 0:
            self._lease.write(b"0")
            self._lease.flush()
        self._lease.seek(0)
        try:
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(self._lease.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(self._lease.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            self._lease.close()
            raise RuntimeError("This API database already has a coordinator; use one server worker") from exc
        self._workers = ThreadPoolExecutor(max_workers=1, thread_name_prefix="civra-api")
        self._closed = False
        with self.repository.connection() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS api_conversations (
                    conversation_id TEXT PRIMARY KEY, case_id TEXT UNIQUE NOT NULL,
                    title TEXT NOT NULL, created_at TEXT NOT NULL,
                    FOREIGN KEY(case_id) REFERENCES case_heads(case_id)
                );
                CREATE TABLE IF NOT EXISTS api_messages (
                    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                    conversation_id TEXT NOT NULL, request_id TEXT NOT NULL,
                    run_id TEXT NOT NULL, previous_run_id TEXT,
                    payload TEXT NOT NULL, status TEXT NOT NULL,
                    created_at TEXT NOT NULL, error TEXT, result_json TEXT,
                    UNIQUE(conversation_id,request_id),
                    FOREIGN KEY(conversation_id) REFERENCES api_conversations(conversation_id)
                );
                CREATE UNIQUE INDEX IF NOT EXISTS api_one_pending
                    ON api_messages(conversation_id) WHERE status IN ('queued','running');
            """)
        self._recover_interrupted()

    def _recover_interrupted(self):
        with self.repository.connection() as db:
            rows = db.execute(
                "SELECT DISTINCT run_id FROM api_messages WHERE status IN ('queued','running')"
            ).fetchall()
        for (run_id,) in rows:
            if self.runner.store.exists(run_id):
                cp = self.runner.store.load_checkpoint(run_id)
                if cp.status == "running":
                    cp.stop("failed", "Server restarted during execution; inspect saved results before retrying")
                    self.runner.store.end(cp)
        with self.repository.connection(write=True) as db:
            db.execute("UPDATE api_messages SET status='failed', error=? WHERE status IN ('queued','running')",
                       ("Execution interrupted by server restart; not automatically retried",))

    def close(self):
        with self._lock:
            self._closed = True
        self._workers.shutdown(wait=True)
        self._lease.close()

    def create(self, title):
        now = datetime.now(timezone.utc)
        case_id, conversation_id = "case_" + uuid4().hex, "conv_" + uuid4().hex
        self.repository.create_case(CaseSnapshot(
            case_id=case_id, case_version=1, synthetic=False,
            dispute_type=None, jurisdiction="CN-Mainland",
            stage="consultation", selected_route=None, created_at=now, updated_at=now,
        ))
        with self.repository.connection(write=True) as db:
            db.execute("INSERT INTO api_conversations VALUES (?,?,?,?)",
                       (conversation_id, case_id, title, now.isoformat()))
        return self.get(conversation_id)

    def context(self, conversation_id):
        with self.repository.connection() as db:
            row = db.execute("SELECT case_id,title,created_at FROM api_conversations WHERE conversation_id=?",
                             (conversation_id,)).fetchone()
        if row is None:
            raise KeyError("conversation not found")
        return {"conversation_id": conversation_id, "case_id": row[0],
                "title": row[1], "created_at": row[2]}

    def _messages(self, conversation_id):
        with self.repository.connection() as db:
            rows = db.execute(
                "SELECT request_id,run_id,status,error,payload,created_at,result_json FROM api_messages "
                "WHERE conversation_id=? ORDER BY sequence", (conversation_id,)).fetchall()
        return [{"request_id": r[0], "run_id": r[1], "status": r[2], "error": r[3],
                 "input": json.loads(r[4]), "created_at": r[5],
                 "run": RunInfo.model_validate_json(r[6]) if r[6] else self._info(r[1])} for r in rows]

    def _info(self, run_id):
        return self.runner.get_run(run_id) if self.runner.store.exists(run_id) else None

    def get(self, conversation_id):
        conversation = self.context(conversation_id)
        messages = self._messages(conversation_id)
        conversation["messages"] = messages
        conversation["run"] = self._info(messages[-1]["run_id"]) if messages else None
        conversation["pending"] = bool(messages and messages[-1]["status"] in ("queued", "running"))
        conversation["case"] = self.repository.get_case(conversation["case_id"]).model_dump(mode="json")
        inventory = self.service.assets.list_case_inventory(conversation["case_id"])
        conversation["files"] = [f.model_dump(mode="json") for f in inventory["files"]]
        conversation["outputs"] = [o.model_dump(mode="json") for o in inventory["outputs"]]
        conversation["inventory_truncated"] = inventory["truncated"]
        conversation["route_progress"] = self.runner.litigation_progress(
            conversation["case_id"], conversation["run"], inventory)
        return conversation

    def receipt(self, conversation_id, request_id):
        with self.repository.connection() as db:
            row = db.execute("SELECT run_id,status,error,result_json FROM api_messages WHERE conversation_id=? AND request_id=?",
                             (conversation_id, request_id)).fetchone()
        if row is None:
            raise KeyError("message not found")
        return MessageReceipt(request_id=request_id, run_id=row[0], status=row[1],
                              error=row[2], run=RunInfo.model_validate_json(row[3]) if row[3] else self._info(row[0]))

    def _input_text(self, case_id, request):
        text = request.text or request.reply.text or ""
        if request.attachments:
            files = []
            for ref in request.attachments:
                record, _ = self.service.assets.get_file(case_id, ref.file_id, ref.file_version)
                files.append(record.model_dump(mode="json"))
            text += "\n用户提交的已上传材料引用（尚未解析或核验）：\n" + json.dumps(files, ensure_ascii=False)
        return text

    def submit(self, conversation_id, request: MessageRequest):
        with self._lock:
            if self._closed:
                raise CapacityError("server is shutting down")
            conversation = self.context(conversation_id)
            payload = json.dumps(request.model_dump(mode="json"), ensure_ascii=False, sort_keys=True)
            with self.repository.connection() as db:
                existing = db.execute(
                    "SELECT payload FROM api_messages WHERE conversation_id=? AND request_id=?",
                    (conversation_id, request.request_id)).fetchone()
            if existing:
                if existing[0] != payload:
                    raise Conflict("request_id already used with different input")
                return self.receipt(conversation_id, request.request_id)
            messages = self._messages(conversation_id)
            if messages and messages[-1]["status"] in ("queued", "running"):
                raise Conflict("conversation already has work in progress")
            last_id = messages[-1]["run_id"] if messages else None
            previous = self._info(last_id) if last_id else None
            case = self.repository.get_case(conversation["case_id"])
            version = case.case_version
            if request.expected_case_version is not None and request.expected_case_version != version:
                raise VersionConflict(f"expected {request.expected_case_version}, current {version}")
            entering = isinstance(request, RouteSubmission) and request.operation == "enter_litigation"
            if isinstance(request, RouteSubmission) and request.event and case.stage != "litigation":
                raise Conflict("请先进入诉讼路线")
            if isinstance(request, RouteSubmission) and request.event:
                try:
                    self.runner.cases.litigation.validate_feedback(case, request.event, request.text or request.reply.text)
                except ValueError as exc:
                    raise Conflict(str(exc)) from exc
            if entering:
                try:
                    action = entry_action(case.stage, previous, request.switch_wait_id)
                except ValueError as exc:
                    raise Conflict(str(exc)) from exc
                if action == "reuse":
                    with self.repository.connection(write=True) as db:
                        db.execute("INSERT INTO api_messages(conversation_id,request_id,run_id,payload,status,created_at,result_json) "
                                   "VALUES (?,?,?,?,?,?,?)", (conversation_id, request.request_id, last_id, payload,
                                   previous.status, datetime.now(timezone.utc).isoformat(), previous.model_dump_json()))
                    return self.receipt(conversation_id, request.request_id)
                run_id = last_id if action == "resume" else "run_" + uuid4().hex
                history_id = last_id
            elif previous and previous.status == "waiting_user":
                if request.reply is None:
                    raise Conflict("answer the pending wait with reply and wait_id")
                if request.wait_id != previous.wait.wait_id:
                    raise StaleWait("wait_id is stale")
                if previous.wait.kind == "route_selection":
                    allowed = {o.route for o in previous.wait.options}
                    if request.reply.route_choice not in allowed:
                        raise Conflict("select a route from the current options")
                elif request.reply.route_choice is not None:
                    raise Conflict("this wait expects answers, not a route")
                elif set(request.reply.answers) - {q.question_id for q in previous.wait.questions}:
                    raise Conflict("answer references an unknown question")
                run_id, history_id = last_id, None
            else:
                if request.reply is not None:
                    raise Conflict("conversation is not waiting for a reply")
                run_id = "run_" + datetime.now().strftime("%Y%m%d_") + uuid4().hex
                history_id = last_id if previous else None
            # Validate every attachment before accepting work or calling a model.
            self._input_text(conversation["case_id"], request)
            if not self._capacity.acquire(blocking=False):
                raise CapacityError("execution queue is full; retry later with the same request_id")
            try:
                with self.repository.connection(write=True) as db:
                    db.execute(
                        "INSERT INTO api_messages(conversation_id,request_id,run_id,previous_run_id,payload,status,created_at) "
                        "VALUES (?,?,?,?,?,'queued',?)",
                        (conversation_id, request.request_id, run_id, history_id, payload,
                         datetime.now(timezone.utc).isoformat()))
                self._workers.submit(self._execute, conversation_id, conversation["case_id"],
                                     run_id, history_id, request)
            except BaseException:
                self._capacity.release()
                with self.repository.connection(write=True) as db:
                    db.execute("UPDATE api_messages SET status='failed',error='Could not schedule execution' "
                               "WHERE conversation_id=? AND request_id=? AND status='queued'",
                               (conversation_id, request.request_id))
                raise
            return self.receipt(conversation_id, request.request_id)

    def _execute(self, conversation_id, case_id, run_id, history_id, request):
        error, result_json, status = None, None, "failed"
        try:
            with self.repository.connection(write=True) as db:
                db.execute("UPDATE api_messages SET status='running' WHERE conversation_id=? AND request_id=?",
                           (conversation_id, request.request_id))
            text = self._input_text(case_id, request)
            # Globally unique trusted correlation ID, bounded independently of user IDs.
            correlation = "api_" + hashlib.sha256(
                (conversation_id + ":" + request.request_id).encode()).hexdigest()
            feedback = request.event if isinstance(request, RouteSubmission) else None
            if isinstance(request, RouteSubmission) and request.operation == "enter_litigation":
                info = self.runner.enter_litigation(
                    case_id, conversation_id=conversation_id, run_id=run_id, history_run_id=history_id,
                    request_id=correlation, actor_id="api_user", switch_wait_id=request.switch_wait_id,
                    expected_case_version=request.expected_case_version)
            elif request.reply is not None:
                reply = request.reply.as_user_reply(request.attachments)
                if request.attachments:
                    reply.text = text
                info = self.runner.resume_run(
                    run_id, reply, wait_id=request.wait_id,
                    conversation_id=conversation_id,
                    expected_case_version=request.expected_case_version,
                    request_id=correlation, actor_id="api_user", litigation_feedback=feedback,
                )
            else:
                info = self.runner.start_run(
                    case_id, text, run_id=run_id, history_run_id=history_id, conversation_id=conversation_id,
                    request_id=correlation, actor_id="api_user", litigation_feedback=feedback,
                    expected_case_version=request.expected_case_version,
                )
            status = info.status
            result_json = info.model_dump_json()
        except Exception as exc:
            # Never echo raw provider exceptions, tokens or material contents.
            log.error("Conversation execution failed: run_id=%s type=%s", run_id, type(exc).__name__)
            status, error = "failed", "Execution failed; inspect the server log and saved run before retrying"
            if self.runner.store.exists(run_id):
                cp = self.runner.store.load_checkpoint(run_id)
                if cp.status == "running":
                    cp.stop("failed", error)
                    self.runner.store.end(cp)
        finally:
            try:
                with self.repository.connection(write=True) as db:
                    db.execute(
                        "UPDATE api_messages SET status=?,error=?,result_json=? WHERE conversation_id=? AND request_id=?",
                        (status, error, result_json, conversation_id, request.request_id))
            finally:
                self._capacity.release()

    def check_run(self, conversation_id, run_id):
        self.context(conversation_id)
        with self.repository.connection() as db:
            row = db.execute("SELECT 1 FROM api_messages WHERE conversation_id=? AND run_id=?",
                             (conversation_id, run_id)).fetchone()
        if row is None:
            raise KeyError("run not found in this conversation")
        return self._info(run_id)

    def run_pending(self, conversation_id, run_id):
        with self.repository.connection() as db:
            return db.execute("SELECT 1 FROM api_messages WHERE conversation_id=? AND run_id=? "
                              "AND status IN ('queued','running')", (conversation_id, run_id)).fetchone() is not None
