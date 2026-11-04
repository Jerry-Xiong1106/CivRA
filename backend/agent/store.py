"""Run persistence: trace (JSONL), events (JSONL) and checkpoints (JSON) under var/runs/{run_id}/.

Records are appended by the runtime around every real call, never reported by
the model (contract §5.2, §6.4).
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Iterator

from pydantic import BaseModel, Field

from backend.common.schemas import (
    FinalOutput, RunConfig, RunEvent, RunStatus, RunUsage, SourceRef, TraceStep, Wait,
)

DEFAULT_RUNS_DIR = Path(os.environ.get("CIVRA_RUNS_DIR", "var/runs"))


class Checkpoint(BaseModel):
    """Everything needed to continue a run in a fresh process."""

    run_id: str
    case_id: str
    status: RunStatus
    config: RunConfig
    messages: list[dict[str, Any]]
    step_id: int = 0
    event_seq: int = 0
    case_version: int
    stage: str
    usage: RunUsage = Field(default_factory=RunUsage)
    wait: Wait | None = None
    experiment_run_id: str | None = None
    start_request_id: str | None = None
    applied_requests: list[str] = Field(default_factory=list)  # resume request_ids already applied
    source_refs: list[SourceRef] = Field(default_factory=list)
    feedback_rounds: dict[str, int] = Field(default_factory=dict)
    limit_notes: list[str] = Field(default_factory=list)  # limits hit, added to open_issues on finish
    final: FinalOutput | None = None
    error: str | None = None
    cancel_requested: bool = False


class RunStore:
    def __init__(self, root: Path | str = DEFAULT_RUNS_DIR) -> None:
        self.root = Path(root)

    def _dir(self, run_id: str) -> Path:
        d = self.root / run_id
        d.mkdir(parents=True, exist_ok=True)
        return d

    def exists(self, run_id: str) -> bool:
        return (self.root / run_id / "checkpoint.json").exists()

    def save_checkpoint(self, cp: Checkpoint) -> None:
        path = self._dir(cp.run_id) / "checkpoint.json"
        tmp = path.with_suffix(".tmp")
        tmp.write_text(cp.model_dump_json(indent=2), encoding="utf-8")
        tmp.replace(path)  # atomic, so a crash never leaves a half-written checkpoint

    def load_checkpoint(self, run_id: str) -> Checkpoint:
        path = self.root / run_id / "checkpoint.json"
        if not path.exists():
            raise KeyError(f"unknown run: {run_id}")
        return Checkpoint.model_validate_json(path.read_text(encoding="utf-8"))

    def list_checkpoints(self) -> Iterator[Checkpoint]:
        if not self.root.exists():
            return
        for p in sorted(self.root.glob("*/checkpoint.json")):
            yield Checkpoint.model_validate_json(p.read_text(encoding="utf-8"))

    def append_step(self, step: TraceStep) -> None:
        self._append(step.run_id, "trace.jsonl", step)

    def append_event(self, event: RunEvent) -> None:
        self._append(event.run_id, "events.jsonl", event)

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

    def read_trace(self, run_id: str) -> list[TraceStep]:
        return [TraceStep.model_validate_json(line) for line in self._lines(run_id, "trace.jsonl")]

    def read_events(self, run_id: str, after_seq: int = 0) -> list[RunEvent]:
        events = (RunEvent.model_validate_json(line) for line in self._lines(run_id, "events.jsonl"))
        return [e for e in events if e.seq > after_seq]

    def _append(self, run_id: str, name: str, record: BaseModel) -> None:
        with open(self._dir(run_id) / name, "a", encoding="utf-8") as f:
            f.write(record.model_dump_json() + "\n")

    def _lines(self, run_id: str, name: str) -> list[str]:
        path = self.root / run_id / name
        if not path.exists():
            return []
        return [line for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def dump(obj: Any) -> str:
    return json.dumps(obj, ensure_ascii=False)
