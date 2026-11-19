"""Native LangChain execution, trusted runtime injection and retry boundaries."""
from backend.common.schemas import RunConfig
import json
from threading import Event

from langchain.tools import tool, ToolRuntime
from langchain_core.callbacks import BaseCallbackHandler
from langchain_core.tools import BaseTool

from backend.agent.case_port import InMemoryCaseStore
from backend.agent.factory import build_runner
from backend.agent.model.llm import ScriptedLLM, tool_call
from backend.agent.runner import AgentRunner
from backend.agent.runtime.store import RunStore
from backend.common.schemas import ToolContext, ToolResult
from backend.tools.policy import parameters_of, set_policy


def make_runner(tmp_path, tools, script):
    cases = InMemoryCaseStore()
    cases.create("native-case", {})
    return AgentRunner(ScriptedLLM(script), tools, cases, RunStore(tmp_path), default_config=RunConfig())


def test_native_toolnode_invocation_preserves_callbacks_and_injects_runtime(tmp_path):
    calls, callbacks = [], []

    class Observe(BaseCallbackHandler):
        def on_tool_start(self, serialized, input_str, **kwargs):
            callbacks.append(serialized["name"])

    @tool
    def inspect_value(value: int, runtime: ToolRuntime[ToolContext]) -> dict:
        """Inspect a value against the current case."""
        calls.append((value, runtime.context, runtime.tool_call_id))
        return ToolResult(status="ok", data={"value": value}).model_dump()

    inspect_value.callbacks = [Observe()]
    assert "runtime" not in parameters_of(inspect_value)["properties"]
    runner = make_runner(tmp_path, [inspect_value], [tool_call("inspect_value", value=7), tool_call("finish", answer="done")])
    info = runner.start_run("native-case", "inspect")
    assert info.status == "completed" and callbacks == ["inspect_value"]
    value, ctx, tool_id = calls[0]
    assert value == 7 and isinstance(ctx, ToolContext)
    assert ctx.case_id == "native-case" and ctx.run_id == info.run_id
    assert ctx.step_id == 2 and ctx.case_version == 1 and tool_id
    assert not hasattr(runner, "registry")


def test_model_cannot_supply_native_runtime_context(tmp_path):
    calls = []

    @tool
    def read_case(runtime: ToolRuntime[ToolContext]) -> dict:
        """Read the current case."""
        calls.append(runtime.context)
        return ToolResult(status="ok").model_dump()

    runner = make_runner(tmp_path, [read_case], [
        tool_call("read_case", runtime={"context": {"case_id": "other-case"}}),
        tool_call("finish", answer="rejected"),
    ])
    info = runner.start_run("native-case", "inspect")
    step = next(s for s in runner.get_trace(info.run_id) if s.tool_name == "read_case")
    assert step.error.code == "INVALID_ARGS" and calls == []
    assert info.usage.tool_attempts == 0


def test_read_retry_uses_same_trusted_deadline_and_records_attempts(tmp_path):
    calls = []

    @tool
    def read_once_more(runtime: ToolRuntime[ToolContext]) -> dict:
        """A read that can recover from a transient upstream failure."""
        calls.append(runtime.context)
        result = (ToolResult.fail("UPSTREAM", "temporary", retryable=True)
                  if len(calls) == 1 else ToolResult(status="ok", summary="recovered"))
        return result.model_dump()

    set_policy(read_once_more, max_retries=1)
    runner = make_runner(tmp_path, [read_once_more], [tool_call("read_once_more"), tool_call("finish", answer="done")])
    info = runner.start_run("native-case", "read")
    assert info.status == "completed"
    assert len(calls) == 2 and calls[0].deadline == calls[1].deadline
    assert info.usage.tool_calls == 1 and info.usage.tool_attempts == 2
    step = next(s for s in runner.get_trace(info.run_id) if s.tool_name == "read_once_more")
    assert step.status == "ok" and step.attempt == 2


def test_timed_out_native_write_is_not_retried_or_reported_as_completed(tmp_path):
    release, finished = Event(), Event()
    calls = []

    @tool
    def write_slowly(runtime: ToolRuntime[ToolContext]) -> dict:
        """A write may still be running after the caller stops waiting."""
        calls.append(runtime.context)
        release.wait(5)
        finished.set()
        return ToolResult(status="ok").model_dump()

    set_policy(write_slowly, side_effects=True, timeout_s=0.02, max_retries=3)

    def report_timeout(messages, tools):
        result = json.loads(messages[-1]["content"])
        assert result["error"]["code"] == "TIMEOUT" and not result["error"]["retryable"]
        return tool_call("finish", answer="write outcome is pending")

    runner = make_runner(tmp_path, [write_slowly], [tool_call("write_slowly"), report_timeout])
    try:
        info = runner.start_run("native-case", "write")
        assert info.status == "completed" and info.usage.tool_attempts == 1
        assert len(calls) == 1 and not finished.is_set()
    finally:
        release.set()
        assert finished.wait(5)


def test_factory_returns_native_tools_without_registration_layer(tmp_path):
    setup = build_runner(config=RunConfig(), llm=ScriptedLLM([]), real_statutes=False, store=RunStore(tmp_path), dotenv=None)
    assert isinstance(setup.tools, list)
    assert all(isinstance(t, BaseTool) for t in setup.tools)
    assert not hasattr(setup, "registry")
