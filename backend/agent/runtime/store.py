"""Durable run metadata, framework history and step/event recording."""
from __future__ import annotations

import json
import os
import sqlite3
import time
from bisect import bisect_right
from collections import OrderedDict
from contextlib import closing, contextmanager
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path
from threading import RLock
from typing import Any, Iterator

from pydantic import BaseModel

from backend.agent.runtime.state import CURRENT_ENGINE, Checkpoint
from backend.common.schemas import TERMINAL_STATUSES, LLMResponse, RunEvent, ToolError, TraceStep, now_iso

DEFAULT_RUNS_DIR = Path(os.environ.get("CIVRA_RUNS_DIR", "var/runs"))
MAX_LOG_INDEXES = 128
_GRAPH_SETUP_LOCK = RLock()


@dataclass
class _LogIndex:
    identity: tuple[int, int]
    rows: list[tuple[int, int]] = field(default_factory=list)
    scanned_bytes: int = 0


class RunStore:
    def __init__(self, root: Path | str = DEFAULT_RUNS_DIR, *, database: Path | str | None = None) -> None:
        self.root = Path(root)
        workspace = self.root.parent if self.root.name == "runs" else self.root
        self.database = Path(database) if database else workspace / "cases.sqlite3"
        self.database.parent.mkdir(parents=True, exist_ok=True)
        self._initialize_runs()
        self._read_lock = RLock()
        self._log_indexes: OrderedDict[Path, _LogIndex] = OrderedDict()

    def _dir(self, run_id: str) -> Path:
        d = self.root / run_id
        d.mkdir(parents=True, exist_ok=True)
        return d

    @contextmanager
    def _db(self):
        with closing(sqlite3.connect(self.database, timeout=10)) as db, db:
            yield db

    def _initialize_runs(self):
        with self._db() as db:
            db.execute("""CREATE TABLE IF NOT EXISTS agent_runs (
                run_id TEXT PRIMARY KEY, case_id TEXT NOT NULL, thread_id TEXT NOT NULL,
                state_json TEXT NOT NULL, cancel_requested INTEGER NOT NULL DEFAULT 0)""")
            db.execute("CREATE INDEX IF NOT EXISTS agent_runs_thread ON agent_runs(thread_id)")
            # Lookups that previously deserialized every run record.
            db.execute("CREATE INDEX IF NOT EXISTS agent_runs_start_request "
                       "ON agent_runs(json_extract(state_json,'$.start_request_id'))")
            db.execute("CREATE INDEX IF NOT EXISTS agent_runs_case_status "
                       "ON agent_runs(case_id, json_extract(state_json,'$.status'))")

    def exists(self, run_id: str) -> bool:
        with self._db() as db:
            return db.execute("SELECT 1 FROM agent_runs WHERE run_id=?", (run_id,)).fetchone() is not None

    def latest_run(self, thread_id):
        with self._db() as db:
            row = db.execute("SELECT run_id FROM agent_runs WHERE thread_id=? ORDER BY rowid DESC LIMIT 1",
                             (thread_id,)).fetchone()
        return row[0] if row else None

    @contextmanager
    def begin_run(self, cp, expected_previous):
        """Reserve the conversation before recording input or invoking tools."""
        with self._db() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT run_id FROM agent_runs WHERE thread_id=? ORDER BY rowid DESC LIMIT 1",
                             (cp.thread_id,)).fetchone()
            if (row[0] if row else None) != expected_previous:
                raise ValueError("会话已有更新的运行。")
            db.execute("INSERT INTO agent_runs(run_id,case_id,thread_id,state_json) VALUES (?,?,?,?)",
                       (cp.run_id, cp.case_id, cp.thread_id, cp.model_dump_json()))
        try:
            yield
        except Exception:
            # No graph or model call has started. Release the reservation so a
            # retry can reuse the application's idempotently recorded input.
            with self._db() as db:
                db.execute("DELETE FROM agent_runs WHERE run_id=?", (cp.run_id,))
            raise

    @contextmanager
    def graph_checkpointer(self):
        """Framework-owned conversation checkpoints, separate from case transactions."""
        from langgraph.checkpoint.sqlite import SqliteSaver
        self.root.mkdir(parents=True, exist_ok=True)
        with closing(sqlite3.connect(self.root / "agent.sqlite3", check_same_thread=False)) as connection:
            saver = SqliteSaver(connection)
            with _GRAPH_SETUP_LOCK:
                saver.setup()
            yield saver

    def request_cancel(self, run_id: str) -> None:
        """A separate durable signal cannot be overwritten by a stale checkpoint."""
        with self._db() as db:
            if not db.execute("UPDATE agent_runs SET cancel_requested=1 WHERE run_id=?", (run_id,)).rowcount:
                raise KeyError(f"unknown run: {run_id}")

    def is_cancel_requested(self, run_id: str) -> bool:
        with self._db() as db:
            row = db.execute("SELECT cancel_requested FROM agent_runs WHERE run_id=?", (run_id,)).fetchone()
        return bool(row and row[0])

    def save_checkpoint(self, cp: Checkpoint) -> None:
        with self._db() as db:
            db.execute("""INSERT INTO agent_runs(run_id,case_id,thread_id,state_json) VALUES (?,?,?,?)
                          ON CONFLICT(run_id) DO UPDATE SET
                          thread_id=excluded.thread_id,state_json=excluded.state_json""",
                       (cp.run_id, cp.case_id, cp.thread_id, cp.model_dump_json()))

    def load_checkpoint(self, run_id: str) -> Checkpoint:
        with self._db() as db:
            row = db.execute("SELECT state_json FROM agent_runs WHERE run_id=?", (run_id,)).fetchone()
        if row is None:
            raise KeyError(f"unknown run: {run_id}")
        record = json.loads(row[0])
        if record.get("engine") != CURRENT_ENGINE:
            raise ValueError("运行记录格式已变化，请新建会话；历史原始文件未改动。")
        return Checkpoint.model_validate(record)

    def run_for_start_request(self, request_id: str) -> str | None:
        """The run a start request already created, for idempotent retries."""
        with self._db() as db:
            row = db.execute("SELECT run_id FROM agent_runs "
                             "WHERE json_extract(state_json,'$.start_request_id')=? "
                             "AND json_extract(state_json,'$.engine')=? LIMIT 1",
                             (request_id, CURRENT_ENGINE)).fetchone()
        return row[0] if row else None

    def running_run(self, case_id: str) -> str | None:
        """A run of this case that is currently executing, if any."""
        with self._db() as db:
            row = db.execute("SELECT run_id FROM agent_runs WHERE case_id=? "
                             "AND json_extract(state_json,'$.status')='running' "
                             "AND json_extract(state_json,'$.engine')=? LIMIT 1",
                             (case_id, CURRENT_ENGINE)).fetchone()
        return row[0] if row else None

    def list_checkpoints(self) -> Iterator[Checkpoint]:
        with self._db() as db:
            rows = db.execute("SELECT state_json FROM agent_runs WHERE json_extract(state_json,'$.engine')=? "
                              "ORDER BY run_id", (CURRENT_ENGINE,)).fetchall()
        yield from (Checkpoint.model_validate_json(row[0]) for row in rows)

    def save_result(self, run_id: str, step_id: int, payload: dict[str, Any]) -> str:
        """Store a step's full input/result; returns the result_ref recorded in the trace."""
        ref = f"results/step_{step_id:04d}.json"
        path = self._dir(run_id) / ref
        path.parent.mkdir(exist_ok=True)
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
        return ref

    def read_result(self, run_id: str, ref: str) -> dict[str, Any]:
        base = (self.root / run_id / "results").resolve()
        path = (self.root / run_id / ref).resolve()
        if base not in path.parents or not path.exists():  # only files under this run's results/
            raise KeyError(f"unknown result_ref for {run_id}: {ref}")
        return json.loads(path.read_text(encoding="utf-8"))

    def read_trace(self, run_id: str, cursor: int = 0, limit: int | None = None) -> list[TraceStep]:
        return [TraceStep.model_validate_json(line)
                for line in self._lines(run_id, "trace.jsonl", cursor=cursor, limit=limit)]

    def read_events(self, run_id: str, after_seq: int = 0) -> list[RunEvent]:
        return [RunEvent.model_validate_json(line)
                for line in self._lines(run_id, "events.jsonl", after_seq=after_seq)]

    def _append(self, run_id: str, name: str, record: BaseModel) -> None:
        with open(self._dir(run_id) / name, "a", encoding="utf-8") as f:
            f.write(record.model_dump_json() + "\n")

    def _lines(self, run_id: str, name: str, *, cursor=0, limit=None, after_seq=None) -> list[bytes]:
        """Index appended complete rows once, then seek directly to requested rows."""
        if cursor < 0 or (limit is not None and limit < 1):
            raise ValueError("cursor must be nonnegative and limit positive")
        path = self.root / run_id / name
        try:
            stream = path.open("rb")
        except FileNotFoundError:
            return []
        with stream, self._read_lock:
            stat = os.fstat(stream.fileno())
            identity = (stat.st_dev, stat.st_ino)
            index = self._log_indexes.get(path)
            if index is None or index.identity != identity or stat.st_size < index.scanned_bytes:
                index = self._log_indexes[path] = _LogIndex(identity)
            self._log_indexes.move_to_end(path)
            if len(self._log_indexes) > MAX_LOG_INDEXES:
                self._log_indexes.popitem(last=False)
            stream.seek(index.scanned_bytes)
            while line := stream.readline():
                if not line.endswith(b"\n"):
                    break  # A writer may still be appending the final row.
                if line.strip():
                    key = json.loads(line)["seq"] if name == "events.jsonl" else len(index.rows)
                    index.rows.append((key, index.scanned_bytes))
                index.scanned_bytes = stream.tell()
            if after_seq is not None:
                cursor = bisect_right(index.rows, after_seq, key=lambda row: row[0])
            selected = index.rows[cursor:] if limit is None else index.rows[cursor:cursor + limit]
            lines = []
            for _, offset in selected:
                stream.seek(offset)
                lines.append(stream.readline())
            return lines

    def read_messages(self, run_id: str) -> list:
        return self._read_channel(run_id, "messages")

    def _read_channel(self, run_id: str, channel: str) -> list:
        """Read one persisted LangGraph state field; old or absent fields default to empty."""
        if not (self.root / "agent.sqlite3").exists():
            return []
        with self.graph_checkpointer() as saver:
            cp = self.load_checkpoint(run_id)
            config = {"thread_id": cp.thread_id}
            if cp.graph_checkpoint_id:
                config["checkpoint_id"] = cp.graph_checkpoint_id
            elif cp.status in TERMINAL_STATUSES:
                return []
            checkpoint = saver.get_tuple({"configurable": config})
            return checkpoint.checkpoint["channel_values"].get(channel, []) if checkpoint else []

    def pin_graph(self, cp):
        """Remember the graph snapshot visible when this invocation stopped."""
        if (self.root / "agent.sqlite3").exists():
            with self.graph_checkpointer() as saver:
                state = saver.get_tuple({"configurable": {"thread_id": cp.thread_id}})
                if state:
                    cp.graph_checkpoint_id = state.checkpoint["id"]

    def continue_thread(self, previous, thread_id):
        """Return the graph checkpoint a new run in the same conversation continues from."""
        if previous.thread_id != thread_id:
            raise ValueError("会话和运行的线程不匹配。")
        source = {"thread_id": thread_id}
        if previous.graph_checkpoint_id:
            source["checkpoint_id"] = previous.graph_checkpoint_id
        with self.graph_checkpointer() as saver:
            saved = saver.get_tuple({"configurable": source})
            if saved is None:
                raise ValueError("缺少上一运行的框架检查点，无法承接历史。")
            return saved.checkpoint["id"]

    def emit(self, cp: Checkpoint, type_: str, step_id: int | None = None, *, checkpoint=False, **data) -> None:
        cp.event_seq += 1
        if checkpoint:
            self.save_checkpoint(cp)
        self._append(cp.run_id, "events.jsonl",
                     RunEvent(seq=cp.event_seq, run_id=cp.run_id, type=type_, step_id=step_id, data=data))

    def end(self, cp: Checkpoint, **data):
        # Publish the terminal event only after its state and final usage are durable.
        self.pin_graph(cp)
        self.emit(cp, f"run.{cp.status}", checkpoint=True, final=cp.final.model_dump(), **data)
        return cp.info()

    @contextmanager
    def step(self, cp: Checkpoint, kind: str, name=None, arguments=None, parent=None):
        """One recording boundary for model, tool and lifecycle steps."""
        cp.step_id += 1
        cp.usage.steps += 1
        started, tick = now_iso(), time.monotonic()
        step = TraceStep(event_id=f"{cp.run_id}:{cp.step_id}", case_id=cp.case_id,
                         run_id=cp.run_id, step_id=cp.step_id, step_type=kind, tool_name=name,
                         arguments=arguments, parent_step_id=parent, status="ok",
                         started_at=started, finished_at=started, case_version=cp.case_version)
        self.emit(cp, "step.started", step.step_id, step_type=kind, tool_name=name, arguments=arguments)
        try:
            yield step
        except Exception as exc:
            step.status = "error"
            step.error = ToolError(code="INTERNAL", message=f"Execution failed ({type(exc).__name__})")
            step.result_summary = step.error.message
            raise
        finally:
            step.finished_at = now_iso()
            step.duration_ms = int((time.monotonic() - tick) * 1000)
            step.case_version = cp.case_version
            self._append(cp.run_id, "trace.jsonl", step)
            self.emit(cp, "step.finished", step.step_id, status=step.status,
                      summary=step.result_summary, duration_ms=step.duration_ms,
                      error=step.error.model_dump() if step.error else None)
            if cp.status not in TERMINAL_STATUSES:
                self.save_checkpoint(cp)

    @staticmethod
    def add_model_usage(cp: Checkpoint, resp: LLMResponse) -> None:
        u = cp.usage
        if resp.usage.prompt_tokens is None or resp.usage.completion_tokens is None:
            u.tokens_complete = False
        u.prompt_tokens += resp.usage.prompt_tokens or 0
        u.completion_tokens += resp.usage.completion_tokens or 0
        if resp.cost is None or u.estimated_cost is None:
            u.estimated_cost = None
        else:
            u.estimated_cost = str(Decimal(u.estimated_cost) + Decimal(resp.cost.amount))
