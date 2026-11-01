# Agent Runtime 接口约定（草案 v0.3）

维护人：成员4（Agent 执行层）  
状态：**草案，待全组确认**。确认后，第 2–5 节的数据结构会落到 `backend/common/` 中的代码定义，以代码为准。

本文档约定 Agent 执行层与其他模块之间的接口：

| 章节 | 内容 | 主要使用方 |
|---|---|---|
| 1 | 总体约定 | 全员 |
| 2 | 工具协议：工具怎样注册、怎样返回结果 | 成员2、成员3 |
| 3 | 模型接口：统一的模型调用入口 | 成员5（基线复用） |
| 4 | 运行接口：启动、继续、取消一次运行 | 成员1、成员5 |
| 5 | 运行事件与执行记录 | 成员1（时间线）、成员5（轨迹评测） |
| 6 | 运行状态、预算与检查点 | 全员了解 |
| 7 | 评测模式接口 | 成员5 |
| 8 | 待确认问题 | 全员 |

各模块内部怎样实现由负责人自己决定，本文档只规定模块之间传递的数据和调用方式。

---

## 1. 总体约定

- **语言**：后端统一使用 Python 3.11+。所有结构可以 JSON 序列化，字段名使用 `snake_case`。
- **数据结构定义**：跨模块的结构使用 Pydantic 模型，放在 `backend/common/`，前端类型由成员1根据这些定义生成或同步。
- **ID**：`case_id`、`run_id`、`step_id` 使用字符串。`run_id` 形如 `run_20261031_ab12cd`，`step_id` 在一次运行内从 1 递增。
- **时间**：ISO 8601 字符串，带时区，例如 `2026-10-31T14:05:00+08:00`。
- **金额**：以“元”为单位的字符串小数，例如 `"12345.60"`，避免浮点误差。
- **版本**：案件状态每次更新时 `case_version` 加 1，输出记录生成时所依据的 `case_version`。
- **负责人边界**（与分工文档一致）：

| 内容 | 定义者 | 本文档中的位置 |
|---|---|---|
| 工具协议、运行事件、检查点、运行状态 | 成员4 | 第 2、4、5、6 节 |
| 来源引用结构 `SourceRef` | 成员2 | 第 2.4 节只列出最低字段 |
| 案件状态、事实、证据、业务输出 | 成员3 | 本文档只引用，不定义 |
| HTTP API、文件存取 | 成员1 | 第 4.3 节给出建议映射 |
| 评测任务、隐藏事实、评分格式 | 成员5 | 第 7 节只约定调用入口 |

---

## 2. 工具协议

工具是 Agent 可以请求调用的 Python 函数。成员2、成员3实现工具函数，按本节格式注册；成员4的 Runtime 负责参数校验、执行、超时、重试、记录，并把结果交回模型。

### 2.0 代码分层

| 目录 | 内容 | 负责人 |
|---|---|---|
| `backend/knowledge/`、`backend/case/` | 工具背后的业务逻辑（检索、证据核对、文书生成等），可以单独调用和测试 | 成员2、成员3 |
| `backend/tools/registry.py` | 工具注册表、参数校验、超时与重试 | 成员4 |
| `backend/tools/<模块>_tools.py` | 每个工具一个薄适配函数：声明 `ToolSpec`，把参数转交给业务逻辑，再包装成 `ToolResult` | 工具负责人（成员2、成员3） |
| `backend/common/` | `ToolSpec`、`ToolResult`、`SourceRef` 等跨模块结构 | 按第 1 节的负责人边界 |

适配函数中不写业务逻辑，业务模块中不依赖 Runtime，这样业务功能可以脱离 Agent 独立测试，基线实验也可以直接复用。

### 2.1 注册信息 `ToolSpec`

```python
class ToolSpec(BaseModel):
    name: str                    # 唯一名称，snake_case，例如 "search_statutes"
    description: str             # 给模型看的说明：什么时候用、能得到什么、不能做什么
    parameters: dict             # 参数的 JSON Schema（type=object）
    owner: str                   # 负责成员，例如 "member2"
    stages: list[str]            # 在哪些案件阶段开放，见第 6.3 节
    timeout_s: float = 30.0      # 单次调用超时
    max_retries: int = 1         # 仅对 retryable 错误重试
    side_effects: bool = False   # 是否修改案件状态或文件，例如 update_case 为 True
```

注册方式：

```python
# backend/tools/knowledge_tools.py
from backend.tools.registry import register_tool
from backend.knowledge import retrieval            # 成员2的业务模块

@register_tool(spec=SEARCH_STATUTES_SPEC)
def search_statutes(args: dict, ctx: ToolContext) -> ToolResult:
    hits = retrieval.search_statutes(**args)        # 业务逻辑
    return ToolResult(status="ok" if hits else "empty", ...)  # 包装为统一格式
```

`description` 会直接进入模型的提示词，写清楚三点：用途、典型输入、返回什么。不要写“必须调用”之类的流程指令，调用时机由 Agent 决定。

### 2.2 调用上下文 `ToolContext`

Runtime 在调用工具时传入，工具不需要自己查找这些信息。

```python
class ToolContext(BaseModel):
    case_id: str
    run_id: str
    step_id: int
    case_version: int            # 本次调用基于的案件版本
    deadline: datetime           # 超过该时间 Runtime 会放弃等待
    stage: str                   # 当前案件阶段
```

工具只能读写 `case_id` 对应的案件数据，不能访问其他案件。

### 2.3 返回结果 `ToolResult`

所有工具统一返回这个结构，不直接抛出异常给 Runtime（未捕获的异常会被 Runtime 记为 `INTERNAL` 错误）。

```python
class ToolResult(BaseModel):
    status: Literal["ok", "empty", "insufficient", "error"]
    data: dict | None = None             # 工具自己的业务结果，结构由工具负责人定义
    summary: str = ""                    # 一两句话概括结果，给模型和时间线显示
    source_refs: list[SourceRef] = []    # 结果依据的法律、案例或用户材料
    error: ToolError | None = None
    case_version: int | None = None      # 修改了案件状态的工具返回新版本号

class ToolError(BaseModel):
    code: Literal["INVALID_ARGS", "NOT_ALLOWED", "NOT_FOUND", "TIMEOUT", "UPSTREAM", "INTERNAL"]
    message: str
    retryable: bool = False
```

| status | 含义 | Agent 的典型反应 |
|---|---|---|
| `ok` | 正常得到结果 | 使用结果，继续下一步 |
| `empty` | 正常执行，但没有找到任何结果 | 改写查询或换工具，最多重复 3 轮 |
| `insufficient` | 有结果，但工具判断依据不足或信息缺失 | 追问用户、补充检索，或在输出中标注缺口 |
| `error` | 执行失败 | 可重试错误由 Runtime 重试；其余交给模型决定 |

错误码中，`INVALID_ARGS`（参数不符合 Schema）、`NOT_ALLOWED`（当前阶段未开放该工具）和 `NOT_FOUND`（工具不存在）由 Runtime 在调用工具之前返回；`TIMEOUT` 由 Runtime 计时；工具未捕获的异常记为 `INTERNAL`。只有 `retryable=True` 的错误会被重试，`TIMEOUT` 默认可重试。

工具只报告“缺什么、发现了什么问题”，**不直接决定追问用户**。是否追问由 Agent 决定（见第 6 节）。例如 `check_evidence` 发现缺少保险信息时，返回 `status="insufficient"`，并在 `data` 中列出缺失字段和建议问题。

### 2.4 来源引用 `SourceRef`（成员2定义，这里只列最低字段）

```python
class SourceRef(BaseModel):
    source_id: str          # 知识库或材料中的唯一 ID
    source_type: Literal["statute", "judicial_interpretation", "case", "template", "guideline", "user_material"]
    title: str              # 法律名称、案号或文件名
    locator: str            # 条款编号、段落号或页码，例如 "第一千二百零八条"、"p.3"
    version: str | None     # 法律版本或生效日期；用户材料为 case_version
    excerpt: str | None     # 原文片段
    url: str | None
```

成员2可以增加字段，但请保留以上字段，Runtime 和前端依赖它们展示和核验引用。

### 2.5 示例

请求（由模型发出，Runtime 校验后执行）：

```json
{
  "tool_name": "search_statutes",
  "arguments": {
    "query": "机动车交通事故 交强险 赔偿顺序",
    "dispute_type": "traffic_accident",
    "incident_date": "2026-05-12",
    "top_k": 5
  }
}
```

返回：

```json
{
  "status": "ok",
  "summary": "找到 3 条相关条文，均为现行有效版本。",
  "data": {"hits": [{"source_id": "civil_code_1213", "score": 0.82}]},
  "source_refs": [
    {
      "source_id": "civil_code_1213",
      "source_type": "statute",
      "title": "中华人民共和国民法典",
      "locator": "第一千二百一十三条",
      "version": "2021-01-01",
      "excerpt": "机动车发生交通事故造成损害，属于该机动车一方责任的，先由承保机动车强制保险的保险人在强制保险责任限额范围内予以赔偿……",
      "url": "https://flk.npc.gov.cn/"
    }
  ],
  "error": null
}
```

### 2.6 计划中的工具清单

工具名称沿用 Proposal 附录 A，具体参数由负责人在注册时给出。

| 工具 | 负责人 | `side_effects` | 开放阶段 |
|---|---|---|---|
| `parse_documents` | 成员3 | 否 | 全部 |
| `update_case` | 成员3 | **是** | 全部 |
| `check_evidence` | 成员3 | 否 | consultation、litigation |
| `search_statutes` | 成员2 | 否 | 全部 |
| `search_cases` | 成员2 | 否 | 全部 |
| `calculate_costs` | 成员3 | 否 | route_selection 及之后 |
| `compare_routes` | 成员3 | 否 | route_selection |
| `get_route_workflow` | 成员3 | 否 | negotiation、mediation、litigation |
| `draft_document` | 成员3 | 否 | litigation |
| `verify_output` | 成员3 | 否 | 全部 |
| `build_visualization` | 成员3 | 否 | 全部 |

说明：`compare_routes` 和 `get_route_workflow` 来自技术方案补充和分工文档，Proposal 表 3 中未单列，是否作为独立工具见第 8 节。

### 2.7 Runtime 内置动作（不是业务工具）

以下动作由 Runtime 自己处理，模型以工具调用的形式发出，其他成员不需要实现：

| 动作 | 作用 |
|---|---|
| `ask_user` | 向用户提出一个或多个问题，运行进入 `waiting_user` |
| `request_route_choice` | 展示路线比较结果，请用户选择路线，运行进入 `waiting_user` |
| `finish` | 结束本次运行，给出最终回答、输出引用及未解决事项 |

```json
{
  "tool_name": "ask_user",
  "arguments": {
    "questions": [
      {"id": "q1", "text": "对方车辆是否投保了交强险？", "reason": "check_evidence 报告缺少保险关系", "field": "insurance.compulsory"}
    ]
  }
}
```

---

## 3. 模型接口

所有模型调用经过同一个入口，Agent 和成员5的基线都使用它，用量统一记录。

```python
class LLMClient:
    def chat(
        self,
        messages: list[dict],          # OpenAI 兼容格式：role/content/tool_calls/tool_call_id
        tools: list[ToolSpec] | None = None,
        *,
        temperature: float = 0.0,
        max_tokens: int | None = None,
    ) -> LLMResponse: ...

class LLMResponse(BaseModel):
    content: str | None
    tool_calls: list[ToolCall]         # ToolCall = {id, tool_name, arguments}
    usage: TokenUsage                  # {prompt_tokens, completion_tokens, cached_tokens}
    model: str
    latency_ms: int
    estimated_cost: str                # 按 config 中的单价计算，单位为元
```

- 采用 OpenAI 兼容的 tool calling 格式，便于切换 DeepSeek 等模型供应商。
- 实现：`backend/agent/llm.py` 中的 `OpenAICompatibleClient`（真实模型）和 `ScriptedLLM`（按脚本回复，用于测试和演示）。
- `chat` 只负责调用和计算用量；在 Agent 运行中，模型调用由 Runtime 写入执行记录。基线直接调用 `chat` 时，用返回的 `usage` 和 `estimated_cost` 自行统计。
- 配置从环境变量读取，API Key 只放在 `.env`，不提交到仓库：

| 变量 | 含义 |
|---|---|
| `CIVRA_LLM_BASE_URL` | OpenAI 兼容接口地址，例如 `https://api.deepseek.com` |
| `CIVRA_LLM_API_KEY` | API Key |
| `CIVRA_LLM_MODEL` | 模型名称 |
| `CIVRA_LLM_PRICE_IN` / `CIVRA_LLM_PRICE_OUT` | 每百万输入 / 输出 token 的价格（元），用于估算费用 |
- 评测时固定 `temperature` 和模型版本，并写入运行记录。

---

## 4. 运行接口

### 4.1 Python 接口

```python
class AgentRunner:
    def start_run(self, case_id: str, user_message: str, *, config: RunConfig | None = None) -> RunInfo: ...
    def resume_run(self, run_id: str, user_reply: UserReply) -> RunInfo: ...
    def cancel_run(self, run_id: str) -> RunInfo: ...
    def get_run(self, run_id: str) -> RunInfo: ...
    def iter_events(self, run_id: str, after_seq: int = 0) -> Iterator[RunEvent]: ...
    def get_trace(self, run_id: str) -> list[TraceStep]: ...

class UserReply(BaseModel):
    text: str | None = None
    answers: dict[str, str] = {}       # 对应 ask_user 的问题 id
    route_choice: str | None = None    # 对应 request_route_choice
    file_ids: list[str] = []           # 本次补充上传的材料

class RunInfo(BaseModel):
    run_id: str
    case_id: str
    status: RunStatus                  # 见第 6.1 节
    pending: dict | None               # waiting_user 时，待回答的问题或待选择的路线
    final: FinalOutput | None          # completed 时的最终结果
    usage: RunUsage                    # 累计调用次数、token、费用、耗时
    error: str | None                  # failed 时的原因
```

- `start_run` 和 `resume_run` 是同步调用：一直执行到运行进入 `waiting_user` 或结束才返回。API 层可以在后台线程中调用，同时用 `iter_events` 向前端推送进度。
- `resume_run` 只能用于 `waiting_user` 状态的运行，否则抛出 `RunStateError`；同一案件已有 `running` 的运行时抛出 `RunConflict`（对应 HTTP 409）。
- `cancel_run` 对 `waiting_user` 的运行立即生效；对 `running` 的运行，在下一步开始前生效。

Runtime 通过一个很小的接口读取案件状态（`backend/agent/case_port.py`）。成员3和成员1确定案件存储后实现这个接口，在此之前使用内存版 `InMemoryCaseStore`：

```python
class CaseStatePort(Protocol):
    def get_snapshot(self, case_id: str) -> CaseSnapshot: ...   # case_id、case_version、stage、summary
    def set_stage(self, case_id: str, stage: str) -> int: ...   # 用户选择路线后调用，返回新的 case_version
```

### 4.2 最终输出 `FinalOutput`

```python
class FinalOutput(BaseModel):
    answer: str                        # 给用户看的回答
    output_refs: list[str]             # 本次生成或更新的业务输出 ID（由成员3的存取接口保存）
    source_refs: list[SourceRef]
    open_issues: list[str]             # 未解决或待用户确认的事项
    case_version: int                  # 输出所依据的案件版本
```

### 4.3 建议的 HTTP 映射（成员1负责实现和最终定稿）

| 方法与路径 | 对应调用 |
|---|---|
| `POST /api/cases/{case_id}/runs` | `start_run` |
| `POST /api/runs/{run_id}/resume` | `resume_run` |
| `POST /api/runs/{run_id}/cancel` | `cancel_run` |
| `GET /api/runs/{run_id}` | `get_run` |
| `GET /api/runs/{run_id}/events?after_seq=0` | `iter_events`，以 SSE 推送 |
| `GET /api/runs/{run_id}/trace` | `get_trace` |

同一案件同时只允许一个处于 `running` 状态的运行，重复启动返回 409。

---

## 5. 运行事件与执行记录

### 5.1 实时事件 `RunEvent`（SSE）

前端用事件更新进度，不需要轮询。断线后用最后收到的 `seq` 重新订阅。

```python
class RunEvent(BaseModel):
    seq: int                   # 本次运行内递增
    run_id: str
    type: Literal[
        "run.started", "run.resumed", "step.started", "step.finished",
        "assistant.delta", "run.waiting_user",
        "run.completed", "run.failed", "run.cancelled", "run.budget_exhausted",
    ]
    step_id: int | None = None
    data: dict = {}
    at: str                    # ISO 8601 时间
```

| 事件 | `data` 内容 |
|---|---|
| `run.started` | `case_id`、`case_version`、`mode` |
| `run.resumed` | `reply`：用户的回答或路线选择 |
| `step.started` | `step_type`、`tool_name`、`arguments` 摘要 |
| `step.finished` | `status`、`summary`、`duration_ms`、`error` |
| `assistant.delta` | 模型回答的增量文本（可选，用于流式显示） |
| `run.waiting_user` | `pending`：问题列表或路线选项 |
| `run.completed` / `run.budget_exhausted` | `final`（`FinalOutput`） |
| `run.failed` / `run.cancelled` | `reason` |

SSE 格式示例：

```text
event: step.finished
data: {"seq": 7, "run_id": "run_20261031_ab12cd", "type": "step.finished", "step_id": 3, "data": {"status": "insufficient", "summary": "缺少保险关系和医疗费票据", "duration_ms": 412}, "at": "2026-10-31T14:05:12+08:00"}
```

### 5.2 执行记录 `TraceStep`

每一步在调用前后由 Runtime 自动追加写入，不依赖模型自己报告。先存为 JSONL（`var/runs/{run_id}.jsonl`），后续需要时迁移到数据库。`data/` 只放法律资料、案例、模板和示例数据；运行时产生的记录和检查点放在 `var/`，该目录已在 `.gitignore` 中，不提交到仓库。

```python
class TraceStep(BaseModel):
    case_id: str
    run_id: str
    step_id: int
    step_type: Literal["model_call", "tool_call", "ask_user", "route_choice", "verify", "finish", "error"]
    tool_name: str | None
    arguments: dict | None
    input_ref: str | None             # 输入材料的引用或版本
    result_summary: str
    result_ref: str | None            # 完整结果的存储位置，避免记录过大
    source_refs: list[SourceRef]
    status: Literal["ok", "empty", "insufficient", "error", "retried", "waiting", "cancelled"]
    error: ToolError | None
    attempt: int = 1                  # 第几次尝试
    started_at: str
    duration_ms: int
    token_usage: TokenUsage | None
    estimated_cost: str | None
    case_version: int
    output_version: int | None
    decision_note: str | None         # 模型对本步动作的简短说明，不是完整推理过程
```

执行记录展示的是实际动作、结果和决策摘要，不代表模型内部的完整思维过程，也不能证明法律判断正确。

---

## 6. 运行状态、预算与检查点

### 6.1 运行状态

```text
running ──► waiting_user ──(resume_run)──► running
   │                                         │
   ├──► completed                            │
   ├──► failed                               │
   ├──► cancelled  ◄──(cancel_run)───────────┘
   └──► budget_exhausted
```

| 状态 | 说明 |
|---|---|
| `running` | 正在执行 |
| `waiting_user` | 已调用 `ask_user` 或 `request_route_choice`，等待用户回复 |
| `completed` | 调用 `finish` 正常结束 |
| `budget_exhausted` | 达到预算上限，输出已完成部分和剩余问题 |
| `failed` | 无法恢复的错误 |
| `cancelled` | 用户取消 |

### 6.2 默认预算（可在 `RunConfig` 中修改）

| 项目 | 默认值 |
|---|---|
| 每次运行最多步数 | 30 |
| 每次运行最多工具调用 | 20 |
| 检索—核验循环 | 3 轮（与计划书一致） |
| 单次工具超时 | 30 秒（工具可在 `ToolSpec` 中另设） |
| 可重试错误的重试次数 | 1 次 |
| 单次运行 token 上限 | 待定，试点后确定 |

### 6.3 案件阶段与工具开放

案件阶段保存在案件状态中，Runtime 读取后决定向模型开放哪些工具。阶段只在用户确认后才推进，例如用户选择路线后才从 `route_selection` 进入对应路线。

| 阶段 | 含义 |
|---|---|
| `consultation` | 收集事实、法律咨询（初始阶段） |
| `route_selection` | 已完成咨询，比较路线、等待用户选择 |
| `negotiation` / `mediation` | 用户选择协商和解或调解 |
| `litigation` | 用户明确选择诉讼准备 |
| `closed` | 用户结束案件 |

协商、调解和诉讼是可选路线，不预设先后顺序，用户可以从任一路线回到 `route_selection` 重新选择。

### 6.4 检查点与恢复

- Runtime 在每个步骤结束、进入 `waiting_user` 时保存检查点：消息历史、`step_id`、预算用量、待回答问题、`case_version`。
- `resume_run` 从最近检查点继续，不重复执行已完成的工具调用。
- 进程中断后，处于 `running` 的运行可从最近检查点继续，或标记为 `failed`。
- 用户补充材料使 `case_version` 增加后，依赖旧版本的输出标记为 `stale`，Agent 在下一次运行中决定是否重新生成。

---

## 7. 评测模式接口

成员5用同一入口批量运行四种模式。Direct LLM、RAG、Fixed Workflow 三个基线由成员5实现，复用 `LLMClient` 和已注册的工具；Agent 模式由成员4提供。

```python
class RunConfig(BaseModel):
    mode: Literal["direct_llm", "rag", "fixed_workflow", "agent"] = "agent"
    model: str | None = None
    temperature: float = 0.0
    max_steps: int = 30
    max_tool_calls: int = 20
    enabled_tools: list[str] | None = None   # None 表示按阶段默认开放
    seed: int | None = None

def run_task(task: EvalTask, config: RunConfig, user_simulator: UserSimulator) -> RunResult: ...

class UserSimulator(Protocol):
    def reply(self, pending: dict) -> UserReply: ...   # 根据隐藏事实池回答追问
```

- 遇到 `waiting_user` 时，`run_task` 调用 `user_simulator.reply`，再 `resume_run`，直到运行结束。
- `RunResult` 至少包含：`FinalOutput`、完整 `TraceStep` 列表、`RunUsage`、最终状态。
- 测试集的标准答案不经过 Runtime，Agent 无法读取。

---

## 8. 待确认问题

| # | 问题 | 需要谁确认 | 草案中的暂定做法 |
|---|---|---|---|
| 1 | 后端语言和 Web 框架 | 全员 | Python 3.11+，Web 框架由成员1决定（建议 FastAPI） |
| 2 | 首先接入哪个模型、谁提供 API Key | 全员 | OpenAI 兼容接口，先接一个支持 tool calling 的模型 |
| 3 | `compare_routes`、`get_route_workflow` 是否作为独立工具 | 成员3 | 暂按独立工具列出 |
| 4 | 案件阶段 `stage` 保存在案件状态还是运行状态 | 成员3 | 保存在案件状态，Runtime 只读取 |
| 5 | 业务输出（证据目录、文书草稿）的存取接口 | 成员1、成员3 | `FinalOutput.output_refs` 只保存 ID |
| 6 | `SourceRef` 的完整字段 | 成员2 | 第 2.4 节为最低字段 |
| 7 | 执行记录先用 JSONL 还是直接用数据库 | 成员1 | 先用 JSONL |
| 8 | 分支名称与合并目标 | 全员 | 按新版分工，成员4使用 `feature/agent-runtime`；仓库管理文档中的分支表需更新。`main`、`develop` 均已设保护，功能分支暂定向 `develop` 提 PR |
| 9 | `backend/tools/` 的维护分工 | 成员2、成员3 | 注册表由成员4维护，各工具适配函数由工具负责人编写（第 2.0 节） |
| 10 | 运行记录与检查点的存放位置 | 成员1 | 暂放 `var/runs/`，不进入 `data/` |

有意见请直接在 PR 中评论，或在群里说明章节号。确认后本文档版本改为 v1.0，之后的修改记录在下方。

## 修改记录

| 版本 | 日期 | 修改内容 |
|---|---|---|
| v0.1 | 2026-10-31 | 初稿 |
| v0.2 | 2026-11-01 | 按仓库目录框架补充工具代码分层（2.0）；运行记录改存 `var/runs/`；补充待确认问题 9、10 |
| v0.3 | 2026-11-01 | 与骨架代码对齐：新增错误码 `NOT_ALLOWED`、事件 `run.resumed` 和 `run.budget_exhausted`；运行接口返回 `RunInfo` 且为同步调用；`chat` 去掉 `run_id` 参数；补充模型环境变量和 `CaseStatePort` |
