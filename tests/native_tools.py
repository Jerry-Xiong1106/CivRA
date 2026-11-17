"""Test-only fixtures for native tools and standalone execution-policy checks.

Production code uses lists of BaseTool directly. This helper preserves concise
fixture setup in the pre-existing behavior tests; AgentRunner only iterates it.
"""
from langchain.tools import tool, ToolRuntime

from backend.common.schemas import ToolContext, ToolResult
from backend.agent.runtime.execution import ToolExecutor
from backend.tools.policy import describe_tool, set_policy


class NativeTools:
    def __init__(self, tools=()):
        self._tools = {}
        self._executor = ToolExecutor()
        self.extend(tools)

    def __iter__(self):
        return iter(self._tools.values())

    def extend(self, tools):
        tools = list(tools)
        names = [t.name for t in tools]
        if len(names) != len(set(names)) or set(names) & self._tools.keys():
            raise ValueError("duplicate tool")
        self._tools.update({t.name: t for t in tools})

    def register(self, spec, handler):
        @tool(spec.name, description=spec.description, args_schema=spec.parameters)
        def native(runtime: ToolRuntime[ToolContext], **args):
            result = handler(args, runtime.context)
            return result.model_dump(mode="json", exclude_none=True) if isinstance(result, ToolResult) else result
        set_policy(native, **spec.model_dump(exclude={"name", "description", "parameters"}))
        self.extend([native])

    def tool(self, spec):
        def decorate(handler):
            self.register(spec, handler)
            return handler
        return decorate

    def names(self):
        return list(self._tools)

    def get_spec(self, name):
        return describe_tool(self._tools[name]) if name in self._tools else None

    def execute(self, name, args, ctx, enabled=None):
        native = self._tools.get(name)
        runtime = ToolRuntime(state={}, context=ctx, config={}, stream_writer=lambda _: None,
                              tool_call_id="test-call", store=None)
        def call():
            result = native.invoke({**args, "runtime": runtime})
            return ToolResult.model_validate(result)
        return self._executor.execute(native, name, args, ctx, call, enabled)

    def close(self, *, wait=True):
        self._executor.close(wait=wait)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()


def tool_fixture(*args, **kwargs):
    from backend.agent.factory import build_tools
    return NativeTools(build_tools(*args, **kwargs))


def mock_tool_fixture(cases):
    from backend.tools.mock_tools import create_mock_tools
    return NativeTools(create_mock_tools(cases))


def register_case_tools(collection, service, **kwargs):
    from backend.tools.case_tools import create_case_tools
    collection.extend(create_case_tools(service, **kwargs))
    return collection


def register_knowledge_tools(collection, search=None, **kwargs):
    from backend.tools.knowledge_tools import create_knowledge_tools
    collection.extend(create_knowledge_tools(search, **kwargs))
