"""Append-only business tool events. Does not manage Agent runs or checkpoints."""
from datetime import datetime,timezone
import json
from uuid import uuid4
from pydantic import Field
from .business_support import deadline, digest, failure
from .contracts import CaseRequest
from .models import BusinessResult, ContractModel
from .repository import VersionConflict, CaseNotFound


class ActivityRequest(CaseRequest):
    after_sequence: int = Field(default=0,ge=0,strict=True)
    through_sequence: int | None = Field(default=None,ge=0,strict=True)
    limit: int = Field(default=100,ge=1,le=200,strict=True)


class ActivityData(ContractModel):
    events: list[dict]
    through_sequence: int
    next_after_sequence: int | None
    scope: str = "业务工具调用记录；不是完整Agent运行日志、模型思考过程或完成百分比"


def observed_usage(result):
    data=result.data
    raw=getattr(data,"model_usage",None)
    if raw is None and hasattr(data,"structured_data"):
        raw=(data.structured_data or {}).get("model_usage")
    if not isinstance(raw,dict): return None
    usage=raw.get("usage") or {};cost=raw.get("cost") or {}
    model=raw.get("model")
    return dict(model=model if isinstance(model,str) and len(model)<=200 else None,
        token_usage={k:v for k,v in usage.items() if k in {"input_tokens","output_tokens","total_tokens","cached_input_tokens"} and type(v) is int and v>=0} if isinstance(usage,dict) else None,
        reported_cost={k:v for k,v in cost.items() if k in {"amount","currency"} and isinstance(v,str) and len(v)<=100} if isinstance(cost,dict) else None,
        scope="仅限业务结果提供的用量；不含完整运行费用，缺失不等于零")


class CaseActivityStore:
    def __init__(self,repository):
        self.repository=repository
        with repository._connection() as connection:
            connection.executescript("""
                CREATE TABLE IF NOT EXISTS case_tool_events (
                    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                    case_id TEXT NOT NULL, attempt_id TEXT NOT NULL,
                    phase TEXT NOT NULL, record_json TEXT NOT NULL,
                    UNIQUE(case_id,attempt_id,phase),
                    FOREIGN KEY(case_id) REFERENCES case_heads(case_id)
                );
                CREATE INDEX IF NOT EXISTS case_tool_events_case_sequence ON case_tool_events(case_id,sequence);
            """)

    def _append(self,record):
        with self.repository._connection(write=True) as connection:
            if record["phase"]=="finished":
                row=connection.execute("SELECT record_json FROM case_tool_events WHERE case_id=? AND attempt_id=? AND phase='started'",
                    (record["case_id"],record["attempt_id"])).fetchone()
                if row is None: raise ValueError("tool finish requires a stored start")
                start=json.loads(row[0])
                if any(record[k]!=start[k] for k in ("request_id","tool_name","run_id","step_id","arguments_digest")):
                    raise ValueError("tool finish correlation differs from its start")
            old=connection.execute("SELECT sequence,record_json FROM case_tool_events WHERE case_id=? AND attempt_id=? AND phase=?",
                (record["case_id"],record["attempt_id"],record["phase"])).fetchone()
            serialized=json.dumps(record,ensure_ascii=False,sort_keys=True,allow_nan=False)
            if old:
                if old[1]!=serialized: raise ValueError("activity event is immutable")
                return old[0]
            return connection.execute("INSERT INTO case_tool_events(case_id,attempt_id,phase,record_json) VALUES (?,?,?,?)",
                (record["case_id"],record["attempt_id"],record["phase"],serialized)).lastrowid

    def begin(self,name,call,*,run_id=None,step_id=None):
        for value in (run_id,step_id):
            if value is not None and (not isinstance(value,str) or not value.strip() or len(value)>200):
                raise ValueError("invalid runtime correlation ID")
        refs={key:value for key,value in call.arguments.items() if key in {
            "file_ids","file_versions","confirmed_fact_ids","output_id","template_id","template_version","rule_version","chart_type"}}
        record=dict(case_id=call.context.case_id,attempt_id="call_"+uuid4().hex,request_id=call.context.request_id,
            run_id=run_id,step_id=step_id,experiment_run_id=call.context.experiment_run_id,tool_name=name,
            phase="started",occurred_at=datetime.now(timezone.utc).isoformat(),case_version=call.case_version,
            arguments_digest=digest(call.arguments),input_refs=refs,status="started",duration_ms=None)
        self._append(record)
        return record

    def finish(self,start,result,duration_ms):
        if type(duration_ms) is not int or duration_ms<0: raise ValueError("invalid duration")
        record={**start,"phase":"finished","occurred_at":datetime.now(timezone.utc).isoformat(),
            "status":result.status,"duration_ms":duration_ms,"result_case_version":result.case_version,
            "output_refs":result.output_refs,"source_refs":[p.model_dump(mode="json") for p in result.source_refs],
            "error":dict(code=result.error.code,retryable=result.error.retryable) if result.error else None,
            "model_usage":observed_usage(result)}
        return self._append(record)

    def query(self,request):
        case_id,version=None,None
        try:
            request=ActivityRequest.model_validate(request.model_dump(mode="json") if hasattr(request,"model_dump") else request)
            case_id,version=request.context.case_id,request.case_version
            deadline(request)
            with self.repository._connection() as connection:
                connection.execute("BEGIN")
                row=connection.execute("SELECT version FROM case_heads WHERE case_id=?",(case_id,)).fetchone()
                if row is None:raise CaseNotFound(case_id)
                if row[0]!=version:raise VersionConflict(version,row[0])
                maximum=connection.execute("SELECT COALESCE(MAX(sequence),0) FROM case_tool_events WHERE case_id=?",(case_id,)).fetchone()[0]
                through=maximum if request.through_sequence is None else request.through_sequence
                if through>maximum: raise ValueError("activity boundary is newer than available events")
                rows=connection.execute("SELECT sequence,record_json FROM case_tool_events WHERE case_id=? AND sequence>? AND sequence<=? ORDER BY sequence LIMIT ?",
                    (case_id,request.after_sequence,through,request.limit+1)).fetchall()
            events=[dict(sequence=row[0],**json.loads(row[1])) for row in rows[:request.limit]]
            deadline(request)
            return BusinessResult[ActivityData](case_id=case_id,case_version=version,status="ok" if events else "empty",
                summary="已读取实际业务工具调用记录；开始记录无结束记录时，运行状态尚不能确定。",
                data=ActivityData(events=events,through_sequence=through,next_after_sequence=events[-1]["sequence"] if len(rows)>request.limit else None))
        except (ValueError,TypeError,KeyError,TimeoutError) as exc:
            return failure(case_id,version,exc)
