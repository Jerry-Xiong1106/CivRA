"""Run metadata and output rules; conversation history belongs to LangGraph."""
from __future__ import annotations

from pydantic import BaseModel, Field

from backend.common.schemas import (
    Citation, FinalOutput, RunConfig, RunInfo, RunStatus, RunUsage, SourceRef, Wait,
)

CURRENT_ENGINE = "langchain-create-agent-v2"


class Checkpoint(BaseModel):
    run_id: str
    engine: str = CURRENT_ENGINE
    case_id: str
    status: RunStatus
    config: RunConfig
    step_id: int = 0
    event_seq: int = 0
    case_version: int
    stage: str
    usage: RunUsage = Field(default_factory=RunUsage)
    wait: Wait | None = None
    experiment_run_id: str | None = None
    start_request_id: str | None = None
    applied_requests: list[str] = Field(default_factory=list)
    source_refs: list[SourceRef] = Field(default_factory=list)
    feedback_rounds: dict[str, int] = Field(default_factory=dict)
    limit_notes: list[str] = Field(default_factory=list)
    final: FinalOutput | None = None
    error: str | None = None

    def info(self) -> RunInfo:
        return RunInfo(run_id=self.run_id, case_id=self.case_id, case_version=self.case_version,
                       experiment_run_id=self.experiment_run_id, status=self.status, wait=self.wait,
                       final=self.final, usage=self.usage, error=self.error)

    def budget_reason(self, active_seconds: float) -> str | None:
        usage, cfg = self.usage, self.config
        limits = (
            (usage.steps, cfg.max_steps, "步数"),
            (usage.model_calls, cfg.max_model_calls, "模型调用"),
            (usage.tool_calls, cfg.max_tool_calls, "工具调用"),
            (usage.prompt_tokens + usage.completion_tokens, cfg.max_total_tokens, "token"),
            (usage.duration_ms / 1000 + active_seconds, cfg.max_run_seconds, "时间"),
        )
        return next((f"达到{label}上限（{limit}）" for used, limit, label in limits
                     if limit is not None and used >= limit), None)

    def complete(self, args: dict, case_version: int) -> None:
        issues = list(dict.fromkeys([*args.get("open_issues", []), *self.limit_notes]))
        known = {source.source_id for source in self.source_refs}
        citations = [Citation(**item) for item in args.get("citations", [])]
        unknown = sorted({citation.source_id for citation in citations} - known)
        if unknown:
            issues.append(f"回答引用了未经检索返回的来源，已移除：{', '.join(unknown)}")
        self.final = FinalOutput(
            answer=args.get("answer", ""), output_refs=args.get("output_refs", []),
            source_refs=self.source_refs, citations=[c for c in citations if c.source_id in known],
            open_issues=issues, case_version=case_version,
        )
        self.status, self.wait = "completed", None

    def stop(self, status: str, reason: str, last_answer: str = "") -> None:
        answer = {"budget_exhausted": "已达到本次运行的预算上限。", "failed": "运行出错，未能完成。",
                  "cancelled": "运行已取消。"}[status]
        self.final = FinalOutput(
            answer=answer + (f"\n\n已完成部分：\n{last_answer}" if last_answer else ""),
            source_refs=self.source_refs, open_issues=[f"{reason}，任务未完成。", *self.limit_notes],
            case_version=self.case_version, completion_reason=status, partial=True,
        )
        self.status, self.wait = status, None
        if status == "failed":
            self.error = reason
