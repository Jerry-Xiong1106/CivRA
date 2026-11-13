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
    from backend.tools.mock_tools import build_mock_registry

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
    config = RunConfig(max_model_calls=0) if status == "budget_exhausted" else RunConfig()
    cases = InMemoryCaseStore()
    cases.create("c", {})
    store = ObservedStore(tmp_path)
    with build_mock_registry(cases) as registry:
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
