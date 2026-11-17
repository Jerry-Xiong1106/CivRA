"""Recording boundaries and incremental reads, using only local temporary files."""
import json
from unittest.mock import patch

import pytest

from backend.agent.runtime.store import RunStore
from backend.common.schemas import RunEvent, TraceStep


def test_events_read_only_new_complete_rows(tmp_path):
    store = RunStore(tmp_path)
    path = tmp_path / "r" / "events.jsonl"
    path.parent.mkdir()
    first = RunEvent(run_id="r", seq=1, type="run.started").model_dump_json()
    next_event = RunEvent(run_id="r", seq=3, type="run.completed").model_dump_json()
    path.write_bytes((first + "\n").encode())
    assert [event.seq for event in store.read_events("r")] == [1]

    # An incomplete concurrent append must neither fail parsing nor consume its cursor.
    with path.open("ab") as stream:
        stream.write(next_event[:20].encode())
    assert store.read_events("r", after_seq=1) == []
    with path.open("ab") as stream:
        stream.write((next_event[20:] + "\n").encode())
    with patch("backend.agent.runtime.store.json.loads", wraps=json.loads) as parse:
        assert [event.seq for event in store.read_events("r", after_seq=2)] == [3]
        assert parse.call_count == 1  # The old event is not parsed again for indexing.
        assert store.read_events("r", after_seq=3) == []
        assert parse.call_count == 1
    assert [event.seq for event in RunStore(tmp_path).read_events("r")] == [1, 3]
    assert [event.seq for event in store.read_events("r", after_seq=0)] == [1, 3]


def test_trace_pages_decode_only_requested_rows_and_follow_appends(tmp_path):
    store = RunStore(tmp_path)
    path = tmp_path / "r" / "trace.jsonl"
    path.parent.mkdir()

    def row(number):
        return TraceStep(event_id=f"r:{number}", case_id="c", run_id="r", step_id=number,
                         step_type="tool_call", status="ok", started_at="start", finished_at="end",
                         case_version=1).model_dump_json() + "\n"

    path.write_text("".join(row(number) for number in range(1, 21)), encoding="utf-8")
    with patch.object(TraceStep, "model_validate_json", wraps=TraceStep.model_validate_json) as decode:
        assert [step.step_id for step in store.read_trace("r", cursor=10, limit=2)] == [11, 12]
        assert decode.call_count == 2
    with path.open("a", encoding="utf-8") as stream:
        stream.write(row(21))
    assert [step.step_id for step in store.read_trace("r", cursor=20, limit=2)] == [21]
    assert [step.step_id for step in store.read_trace("r", cursor=0, limit=1)] == [1]
    # Rebuilding a file invalidates the old offsets.
    path.write_text(row(1), encoding="utf-8")
    assert [step.step_id for step in store.read_trace("r")] == [1]


@pytest.mark.parametrize("status", ["completed", "failed", "budget_exhausted"])
def test_terminal_checkpoint_saved_once_before_terminal_event(tmp_path, status):
    from backend.agent.case_port import InMemoryCaseStore
    from backend.agent.model.llm import ScriptedLLM, tool_call
    from backend.agent.runner import AgentRunner
    from backend.common.schemas import TERMINAL_STATUSES, RunConfig
    from tests.native_tools import mock_tool_fixture

    class ObservedStore(RunStore):
        terminal_writes = 0
        published = None

        def save_checkpoint(self, cp):
            if cp.status in TERMINAL_STATUSES:
                self.terminal_writes += 1
            super().save_checkpoint(cp)

        def _append(self, run_id, name, record):
            if name == "events.jsonl" and record.type == f"run.{status}":
                self.published = self.load_checkpoint(run_id)
                assert self.published.status == status
                assert self.published.event_seq == record.seq
            super()._append(run_id, name, record)

    def fail(messages, tools):
        raise ConnectionError("offline failure")

    script = [tool_call("search_statutes", query="q"), tool_call("search_statutes", query="q"),
              tool_call("finish", answer="done")]
    if status == "failed":
        script = [script[0], fail]
    config = RunConfig(conversation_route="case_work", max_model_calls=0) if status == "budget_exhausted" else RunConfig(conversation_route="case_work", )
    cases = InMemoryCaseStore()
    cases.create("c", {})
    store = ObservedStore(tmp_path)
    with mock_tool_fixture(cases) as registry:
        runner = AgentRunner(ScriptedLLM(script), registry, cases, store, config)
        info = runner.start_run("c", "hi")
    assert info.status == status and store.terminal_writes == 1
    assert store.published.usage == info.usage
    assert store.published.final == info.final
    assert sum(event.type == f"run.{status}" for event in store.read_events(info.run_id)) == 1


def test_compaction_serializes_each_message_view_once():
    from collections import Counter
    from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
    from backend.agent.model import prompts

    history = [HumanMessage(content="first question")]
    for number in range(20):
        history.extend([
            AIMessage(content="", tool_calls=[{"id": str(number), "name": "lookup", "args": {}}]),
            ToolMessage(content=json.dumps({"status": "ok", "summary": "found", "data": "x" * 2000}),
                        tool_call_id=str(number)),
        ])
    with patch.object(prompts, "message_dict", wraps=prompts.message_dict) as convert:
        result = prompts.compact_history(history, 500)
        counts = Counter(id(call.args[0]) for call in convert.call_args_list)
        assert max(counts.values()) == 1
    assert result[0] is history[0]
    assert len(result) < len(history)
    called = {call["id"] for message in result if isinstance(message, AIMessage) for call in message.tool_calls}
    answered = {message.tool_call_id for message in result if isinstance(message, ToolMessage)}
    assert called == answered
    assert len(json.loads(history[2].content)["data"]) == 2000


def _checkpoint():
    from backend.agent.runtime.state import Checkpoint
    from backend.common.schemas import RunConfig
    return Checkpoint(run_id="r", case_id="c", status="running",
                      config=RunConfig(), case_version=1, stage="consultation")


def test_checkpoint_recovers_from_windows_replace_contention(tmp_path):
    from pathlib import Path

    store = RunStore(tmp_path)
    cp = _checkpoint()
    store.save_checkpoint(cp)
    cp.status = "waiting_user"
    replace = Path.replace
    busy = PermissionError("simulated Windows file contention")
    busy.winerror = 5
    attempts = []

    def replace_once_busy(source, target):
        attempts.append(source)
        if len(attempts) < 3:
            raise busy
        return replace(source, target)

    with patch.object(Path, "replace", replace_once_busy), patch(
        "backend.agent.runtime.store.time.sleep"
    ) as sleep:
        store.save_checkpoint(cp)
    assert len(attempts) == 3 and sleep.call_count == 2
    assert store.load_checkpoint("r").status == "waiting_user"
    assert not list((tmp_path / "r").glob("*.tmp"))


def test_checkpoint_permanent_failure_preserves_previous_and_pending_state(tmp_path):
    from pathlib import Path

    store = RunStore(tmp_path)
    cp = _checkpoint()
    store.save_checkpoint(cp)
    cp.status = "waiting_user"
    busy = PermissionError("permanent Windows access failure")
    busy.winerror = 5
    with patch.object(Path, "replace", side_effect=busy) as replace, patch(
        "backend.agent.runtime.store.time.sleep"
    ):
        with pytest.raises(PermissionError):
            store.save_checkpoint(cp)
    assert replace.call_count == 7
    assert store.load_checkpoint("r").status == "running"
    pending, = (tmp_path / "r").glob("*.tmp")
    assert json.loads(pending.read_text(encoding="utf-8"))["status"] == "waiting_user"


def test_progress_reads_and_checkpoint_writes_can_run_concurrently(tmp_path):
    from concurrent.futures import ThreadPoolExecutor

    store = RunStore(tmp_path)
    cp = _checkpoint()
    store.save_checkpoint(cp)

    def write():
        for number in range(100):
            cp.step_id = number
            store.save_checkpoint(cp)

    def read():
        for _ in range(200):
            assert store.load_checkpoint("r").run_id == "r"
            assert [c.run_id for c in store.list_checkpoints()] == ["r"]

    with ThreadPoolExecutor(max_workers=3) as pool:
        futures = [pool.submit(write), pool.submit(read), pool.submit(read)]
        for future in futures:
            future.result(timeout=30)
    assert store.load_checkpoint("r").step_id == 99


def test_real_windows_open_reader_retries_checkpoint_replace(tmp_path):
    import sys
    if sys.platform != "win32":
        pytest.skip("Windows file sharing behavior")

    store = RunStore(tmp_path)
    cp = _checkpoint()
    store.save_checkpoint(cp)
    cp.status = "waiting_user"
    # A separate reader with no delete-sharing reproduces the OS failure.
    with (tmp_path / "r" / "checkpoint.json").open("rb") as reader:
        def release_reader(_seconds):
            reader.close()

        with patch("backend.agent.runtime.store.time.sleep", side_effect=release_reader) as sleep:
            store.save_checkpoint(cp)
        assert sleep.call_count >= 1
    assert store.load_checkpoint("r").status == "waiting_user"
