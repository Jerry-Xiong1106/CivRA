"""Project execution policy attached to native LangChain tools as metadata."""
from langchain_core.tools import BaseTool
from pydantic import BaseModel, Field

from backend.common.schemas import ALL_STAGES, ToolSpec


class ToolPolicy(BaseModel):
    owner: str = "external"
    stages: list[str] = Field(default_factory=lambda: list(ALL_STAGES))
    timeout_s: float = Field(default=30.0, gt=0)
    max_retries: int = Field(default=1, ge=0)
    side_effects: bool = False


def set_policy(tool: BaseTool, **policy) -> BaseTool:
    """Attach project policy; this does not wrap or register the native tool."""
    tool.metadata = {**(tool.metadata or {}), "civra": ToolPolicy(**policy).model_dump()}
    return tool


def policy_of(tool: BaseTool) -> ToolPolicy:
    return ToolPolicy.model_validate((tool.metadata or {}).get("civra", {}))


def parameters_of(tool: BaseTool) -> dict:
    if isinstance(tool.args_schema, dict):
        return tool.args_schema
    schema = tool.tool_call_schema
    return schema if isinstance(schema, dict) else schema.model_json_schema()


def describe_tool(tool: BaseTool) -> ToolSpec:
    """Manifest view of a native tool: its own name/description/schema plus project policy."""
    return ToolSpec(name=tool.name, description=tool.description,
                    parameters=parameters_of(tool), **policy_of(tool).model_dump())
