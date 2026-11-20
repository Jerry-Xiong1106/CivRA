# Agent Runtime 接口约定（草案 v0.8）

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
| `backend/agent/runtime/middleware.py`、`execution.py` | 原生工具的上下文、阶段校验、超时、重试和轨迹 | 成员4 |
| `backend/tools/policy.py` | 原生工具metadata中的项目执行策略 | 成员4 |
| `backend/tools/<模块>_tools.py` | 每个工具一个薄适配函数：通过原生 `@tool` 定义，把参数转交给业务逻辑，返回 `ToolResult` 的字典表示 | 工具负责人（成员2、成员3） |
| `backend/common/` | `ToolSpec`、`ToolResult`、`SourceRef` 等跨模块结构 | 按第 1 节的负责人边界 |

适配函数中不写业务逻辑，业务模块中不依赖 Runtime，这样业务功能可以脱离 Agent 独立测试，基线实验也可以直接复用。

### 2.1 原生工具定义和项目策略

2026-11-16更新：工具名称、说明及参数由LangChain原生`@tool`定义；ToolPolicy负责阶段、超时、重试、副作用及反馈限制，通过`set_policy`附加到`tool.metadata["civra"]`。下列ToolSpec仅保留为契约/manifest导出结构，不再用于注册和执行。

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
    feedback_loop: str | None = None  # 所属的反馈循环，例如 "retrieve_verify"，见第 6.2 节
```

`search_statutes`、`search_cases` 和 `verify_output` 请设置 `feedback_loop="retrieve_verify"`，Runtime 据此限制检索—核验的重复轮数。

原生工具定义与装配示例（业务函数由调用方提供）：

```python
from langchain.tools import tool, ToolRuntime
from backend.common.schemas import ToolContext
from backend.tools.policy import set_policy

def create_tools(business_search):
    @tool
    def search_statutes(query: str, runtime: ToolRuntime[ToolContext]) -> dict:
        """检索法律条文并返回候选来源。"""
        result = business_search(query, deadline=runtime.context.deadline)
        return result.model_dump(mode="json", exclude_none=True)

    set_policy(search_statutes, owner="member2", feedback_loop="retrieve_verify")
    return [search_statutes]

# tools列表直接交给AgentRunner；runner把它与内置控制工具一起传给create_agent。
```

`description` 会直接进入模型的提示词，写清楚三点：用途、典型输入、返回什么。不要写“必须调用”之类的流程指令，调用时机由 Agent 决定。

### 2.2 调用上下文 `ToolContext`

Runtime在调用前创建下列上下文，通过框架注入的`ToolRuntime[ToolContext].context`传入；runtime不出现在模型参数Schema中。每次调用取得独立上下文快照。

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

原生业务工具返回这个结构的`model_dump(mode="json", exclude_none=True)`字典，由LangChain包装为ToolMessage；不直接抛出异常给Runtime（未捕获的异常会被 Runtime 记为 `INTERNAL` 错误）。

```python
class ToolResult(BaseModel):
    model_config = {"extra": "forbid"}    # 不接受旧顶层source_refs等未声明字段

    status: Literal["ok", "empty", "insufficient", "error"]
    data: dict | None = None             # 工具自己的业务结果，结构由工具负责人定义
    summary: str = ""                    # 一两句话概括结果，给模型和时间线显示
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

错误码中，`INVALID_ARGS`（参数不符合 Schema）、`NOT_ALLOWED`（当前阶段未开放该工具）和 `NOT_FOUND`（工具不存在）由 Runtime 在调用工具之前返回；`TIMEOUT` 由 Runtime 计时；工具未捕获的异常记为 `INTERNAL`。仅无副作用的工具在`retryable=True`、重试次数未耗尽且deadline仍有余量时自动重试。所有尝试共用ToolContext.deadline；Agent为一次逻辑工具调用分配ToolPolicy.timeout_s总时限。超时后取消尚未开始的任务，已运行的线程不能强制终止。检索服务把deadline继续传递给embedding HTTP调用，注入的embedding客户端应支持可选deadline关键字参数。

工具只报告“缺什么、发现了什么问题”，**不直接决定追问用户**。是否追问由 Agent 决定（见第 6 节）。例如 `check_evidence` 发现缺少保险信息时，返回 `status="insufficient"`，并在 `data` 中列出缺失字段和建议问题。

### 2.4 来源引用 `SourceRef`（成员2定义）

```python
class SourceLocator(BaseModel):
    article: str | None = None
    page_start: int | None = None
    page_end: int | None = None
    paragraph: str | None = None
    chunk_id: str | None = None

class SourceRef(BaseModel):
    source_id: str          # 知识库或材料中的唯一 ID
    source_type: Literal["statute", "judicial_interpretation", "case", "template", "guideline", "user_material"]
    title: str              # 法律名称、案号或文件名
    locator: SourceLocator  # 仅接受结构化定位
    source_version: str  # 来源版本必填，公布日期未知时使用正文哈希
    effective_from: str | None = None
    effective_to: str | None = None
    decision_date: str | None = None
    retrieved_at: str | None = None
    retrieved_date: str | None = None  # 只有获取日期时保留日期，不能编造时间戳
    source_file: str | None = None
    content_sha256: str | None = None
    url: str | None
```

SourceLocator至少有一种定位信息，页码为正整数且起始页不能大于结束页。SourceRef只接受结构化locator和source_version，不再定义version、excerpt或字符串定位兼容。工具引用仅保存在data.matches[].source_ref，正文仅在matches[].text；Runtime从这些匹配项派生TraceStep/FinalOutput.source_refs。

### 2.5 法规工具示例

请求参数只包含query和top_k。下例摘录返回的主要字段，真实引用还包含页码、来源URL、效力日期和内容哈希；正文只保存一份。

~~~json
{
  "status": "ok",
  "summary": "找到 1 条候选；候选不代表已有充分法律依据。",
  "data": {
    "schema_version": "0.1",
    "query": "民法典第一千二百一十三条",
    "matches": [{
      "text": "检索到的完整法条正文（此处仅示意字段）",
      "source_ref": {
        "source_id": "中华人民共和国民法典::2020-05-28::第一千二百一十三条",
        "source_type": "statute",
        "title": "中华人民共和国民法典",
        "source_version": "2020-05-28",
        "locator": {"article": "第一千二百一十三条"}
      },
      "rank": 1,
      "score": 1.0,
      "score_type": "exact"
    }],
    "empty_reason": null,
    "warnings": [],
    "knowledge_version": null
  },
  "error": null
}
~~~

knowledge_version为null表示此示例没有绑定实际知识库快照；默认真实service会提供快照标识。旧工具顶层source_refs字段不再接受；旧日志仍可按原始记录读取。引用与空结果的可运行样例见[RAG交接说明](rag/rag_agent_handoff.md)。

### 2.6 计划中的工具清单

工具名称沿用 Proposal 附录 A，具体参数由负责人在注册时给出。

`search_statutes` 在现有 `backend/tools/knowledge_tools.py` 接入成员2的 `RetrievalService.search()`，默认 `exact-vector`＋metadata；离线关键词实验须显式设置 `CIVRA_RETRIEVAL_MODE=keyword`。工具只接受query/top_k，incident_date、dispute_type、filters等未实现参数明确拒绝。结果统一为data.matches及data.empty_reason，不返回重复hits；正常无匹配映射empty，空白/符号/不支持的条号格式映射INVALID_ARGS，超时及embedding失败分别映射TIMEOUT/UPSTREAM。embedding内部不重试，重试由注册表执行。factory支持注入retrieval_service以复用资源，也保留statute_search回调供测试；两者不可同时传入。

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
    "kind": "clarification",
    "questions": [
      {"question_id": "q1", "text": "对方车辆是否投保了交强险？", "reason": "check_evidence 报告缺少保险关系", "field": "insurance.compulsory"}
    ]
  }
}
```

`kind` 为 `clarification`（追问事实，默认）或 `material_request`（请用户上传材料）。`finish` 可以带 `citations`：`[{source_id, location}]`，说明回答中哪句话依赖哪个来源；只能引用工具实际返回过的来源，其他的会被移除并记入 `open_issues`。

---

## 3. 模型接口

Agent与项目内评测基线共享LangChain BaseChatModel。Agent由create_agent管理模型/工具循环；单次生成基线直接invoke模型，不获得动态工具规划能力。统一转换函数仅整理用量与轨迹字段。

```python
# model: BaseChatModel = llm_from_env()
# Agent: create_agent(model, tools=..., middleware=..., checkpointer=...)
# 单次基线: model.bind(...).invoke(messages)
# 下列LLMResponse仅用于项目记录及评测返回格式，不是另一套模型客户端。

class LLMResponse(BaseModel):
    content: str | None
    tool_calls: list[ToolCall]         # ToolCall = {id, tool_name, arguments}；arguments 已解析为 JSON 对象
    finish_reason: str | None          # 供应商返回值，如 "stop"、"tool_calls"、"length"
    usage: TokenUsage
    cost: CostInfo | None              # 未配置单价时为 None（未知，不是 0）
    model: str                         # 供应商实际返回的模型名称
    provider_request_id: str | None
    latency_ms: int

class TokenUsage(BaseModel):           # 供应商未报告的字段为 None
    prompt_tokens: int | None
    completion_tokens: int | None
    total_tokens: int | None
    cached_tokens: int | None          # 已包含在 prompt_tokens 中，不要重复相加
    attempt_count: int = 1             # 本次调用的 HTTP 请求次数；客户端目前不自动重试

class CostInfo(BaseModel):
    amount: str                        # 十进制字符串
    currency: str = "CNY"
    basis: Literal["estimated", "billed"] = "estimated"
    price_version: str | None
```

- 采用 OpenAI 兼容的 tool calling 格式，便于切换 DeepSeek 等模型供应商。
- 模型适配位于backend/agent/model/llm.py：make_chat_model返回ChatOpenAI，response_from_message统一用量/费用/来源请求ID，ScriptedLLM也是BaseChatModel，离线测试与真实Agent使用相同create_agent执行路径。字典输入转换使用LangChain原生convert_to_messages，提示压缩直接操作原生消息。
- runner.py保留开始、恢复、取消和导出接口。runtime/middleware.py直接提供框架钩子、案件刷新、模型及工具适配；runtime/state.py保存运行元数据及输出规则；runtime/store.py通过统一step上下文管理器记录步骤、事件和用量。execution.py和独立recorder已删除，middleware不回调Runner私有方法。
- 同步invoke设置max_concurrency=1，由框架维持同批工具顺序；追问或结束后，剩余工具只返回未执行回执，不进行副作用。当前入口没有提供异步ainvoke的顺序保证。
- 原生HTTP客户端、lc_model.py、lc_tools.py已移除。项目内Direct/RAG基线已同步改为BaseChatModel.invoke；用量仍映射为原评测字段，不重跑或覆盖历史真实输出。
- 模型max_retries固定0，每次模型执行尝试都计数；发生异常时用量/费用保持未知并保存脱敏错误。工具重试继续由注册表执行，当前评测计划支持model_retries=0、tool_retries=1，其他设置明确报错。
- `seed` 会原样传给供应商，但不是所有供应商都支持；是否生效以供应商说明为准。Agent 运行中，每次模型调用的参数都写在执行记录的 `arguments` 里。
- 配置从环境变量读取，API Key 只放在 `.env`，不提交到仓库：

| 变量 | 含义 |
|---|---|
| `CIVRA_LLM_BASE_URL` | OpenAI 兼容接口地址，例如 `https://api.deepseek.com` |
| `CIVRA_LLM_API_KEY` | API Key |
| `CIVRA_LLM_BACKEND` | 已废弃；删除旧native设置，当前仅使用LangChain |
| `CIVRA_LLM_MODEL` | 模型名称 |
| `CIVRA_LLM_PRICE_IN` / `CIVRA_LLM_PRICE_OUT` | 每百万输入 / 输出 token 的价格（元），用于估算费用；不填时费用为未知 |
| `CIVRA_LLM_PRICE_VERSION` | 价格版本，例如核对价格的日期 |
- 评测时固定 `temperature` 和模型版本，并写入运行记录。

---

## 4. 运行接口

### 4.1 Python 接口

```python
class AgentRunner:
    def start_run(self, case_id: str, user_message: str, *, config: RunConfig | None = None,
                  experiment_run_id: str | None = None, request_id: str | None = None) -> RunInfo: ...
    def resume_run(self, run_id: str, reply: UserReply, *, wait_id: str,
                   expected_case_version: int | None = None, request_id: str | None = None) -> RunInfo: ...
    def cancel_run(self, run_id: str, *, request_id: str | None = None) -> RunInfo: ...
    def get_run(self, run_id: str) -> RunInfo: ...
    def iter_events(self, run_id: str, after_seq: int = 0) -> Iterator[RunEvent]: ...
    def get_trace(self, run_id: str) -> list[TraceStep]: ...
    def export_trace(self, run_id: str, cursor: int = 0, limit: int = 100) -> TraceExport: ...
    def get_step_result(self, run_id: str, result_ref: str) -> dict: ...
    def get_runtime_manifest(self) -> RuntimeManifest: ...

class UserReply(BaseModel):
    text: str | None = None
    answers: dict[str, str] = {}       # question_id -> 回答
    route_choice: str | None = None    # 对应 route_selection
    file_ids: list[str] = []           # 本次补充上传的材料

class Wait(BaseModel):                 # 运行在等待什么
    wait_id: str                       # resume_run 必须带上
    kind: Literal["clarification", "material_request", "route_selection"]
    questions: list[WaitQuestion]      # {question_id, text, reason, field}
    options: list[WaitOption]          # route_selection 时的选项 {route, label}
    comparison: str | None             # route_selection 时的路线比较说明

class RunInfo(BaseModel):
    schema_version: str                # 本文档版本
    run_id: str
    case_id: str
    case_version: int
    experiment_run_id: str | None      # 评测方的实验记录 ID，原样保存
    status: RunStatus                  # 见第 6.1 节
    wait: Wait | None                  # waiting_user 时不为空
    final: FinalOutput | None          # 结束时的结果；提前结束时为已完成的部分
    usage: RunUsage                    # 权威用量总计，见第 6.2 节
    error: str | None                  # failed 时的原因
    stale: bool                        # 最终结果依据的案件版本是否已过时（第 6.4 节）
```

- `start_run` 和 `resume_run` 是同步调用：一直执行到运行进入 `waiting_user` 或结束才返回，执行时间受 `max_run_seconds` 限制。API 层可以在后台线程中调用，同时用 `iter_events` 向前端推送进度。因为是同步执行，不存在 `queued` 状态。
- `resume_run` 的检查顺序（任何一项不通过都不会执行任何步骤）：
  1. `request_id` 已经处理过：直接返回当前状态，不再执行。客户端超时后可以用同一个 `request_id` 安全重试。
  2. 运行不在 `waiting_user`：抛出 `RunStateError`。
  3. `wait_id` 不是当前等待：抛出 `StaleWait`。
  4. 提供了 `expected_case_version`，但和案件当前版本不同：抛出 `VersionConflict`。
  5. 同一案件已有 `running` 的运行：抛出 `RunConflict`（对应 HTTP 409）。
- `start_run` 带相同的 `request_id` 重复调用时，返回第一次创建的运行，不会重复启动。
- `cancel_run` 对 `waiting_user` 的运行立即生效；对 `running` 的运行，在下一步开始前生效。
- `export_trace` 按步骤顺序分页返回执行记录。`cursor` 是已读取的步骤数，下一页传入上次返回的 `next_cursor`；`complete=True` 表示运行已结束且全部记录已返回。运行尚未结束时，`next_cursor` 不为空，之后还可能有新步骤。
- `get_step_result` 根据执行记录中的 `result_ref` 读取该步骤的完整参数和返回结果（模型调用为请求参数和完整响应），只能读取同一运行下的结果文件。
- `get_runtime_manifest` 返回运行时版本、系统提示词的 SHA-256、每个工具 Schema 的 SHA-256 及整体哈希、模型客户端和默认模型、默认 `RunConfig`。不包含 API Key 或接口地址。评测时可以用它确认四种模式用的是同一套配置。

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
    source_refs: list[SourceRef]       # 本次运行中工具返回过的全部来源
    citations: list[Citation]          # {source_id, location}：回答中哪句话依赖哪个来源
    open_issues: list[str]             # 未解决或待用户确认的事项
    case_version: int                  # 输出所依据的案件版本
    completion_reason: Literal["finished", "budget_exhausted", "failed", "cancelled"]
    partial: bool                      # 提前结束时为 True，answer 中保留模型最后一次的文字说明
```

运行失败、取消或达到预算上限时，`final` 仍然会返回，内容是已完成的部分：已检索到的来源、模型最后一次给出的文字说明，以及说明原因的 `open_issues`。

RunInfo.tasks 按运行固定的图检查点读取。同一会话的运行共享 thread_id = conversation_id，运行完成不会关闭任务或案件。

### 4.3 建议的 HTTP 映射（成员1负责实现和最终定稿）

当前已实现会话式 HTTP 层，前端接入以 [API 文档](../backend/api/README.md) 为准。下表保留早期按 run 暴露接口的建议；实际接口由 conversation 协调 start/resume，并补齐上传、下载和持续历史，不逐项照搬下表。


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
| `run.started` | `case_id`、`case_version`、`mode`、`experiment_run_id` |
| `run.resumed` | `wait_id`、`request_id`、`reply`：用户的回答或路线选择 |
| `step.started` | `step_type`、`tool_name`、`arguments` 摘要 |
| `step.finished` | `status`、`summary`、`duration_ms`、`error` |
| `assistant.delta` | 模型回答的增量文本（可选，用于流式显示） |
| `run.waiting_user` | `wait`（`Wait`） |
| `run.completed` | `final`（`FinalOutput`） |
| `run.budget_exhausted` / `run.failed` / `run.cancelled` | `reason`、`final`（已完成的部分） |

SSE 格式示例：

```text
event: step.finished
data: {"seq": 7, "run_id": "run_20261031_ab12cd", "type": "step.finished", "step_id": 3, "data": {"status": "insufficient", "summary": "缺少保险关系和医疗费票据", "duration_ms": 412}, "at": "2026-10-31T14:05:12+08:00"}
```

### 5.2 执行记录 `TraceStep`

每一步由 middleware 和生命周期适配记录。runs/<run_id>/ 保存 trace.jsonl、events.jsonl 和 results/step_NNNN.json。运行状态及取消标记保存在 cases.sqlite3 的 agent_runs 表。

```python
class TraceStep(BaseModel):
    event_id: str                     # f"{run_id}:{step_id}"，全局唯一
    case_id: str
    run_id: str
    step_id: int                      # 运行内从 1 递增
    parent_step_id: int | None        # 工具步骤：发起它的那次 model_call 的 step_id
    step_type: Literal["model_call", "tool_call", "ask_user", "route_choice", "verify", "finish", "error"]
    tool_name: str | None
    arguments: dict | None            # 工具参数；model_call 为模型参数（model、temperature、seed 等）
    input_ref: str | None             # 输入材料的引用或版本
    result_summary: str
    result_ref: str | None            # 完整结果，用 get_step_result 读取
    source_refs: list[SourceRef]
    status: Literal["ok", "empty", "insufficient", "error", "retried", "waiting", "cancelled"]
    error: ToolError | None
    attempt: int = 1                  # 实际执行次数，包含重试
    started_at: str
    finished_at: str
    duration_ms: int
    token_usage: TokenUsage | None    # 仅 model_call
    cost: CostInfo | None             # 仅 model_call
    case_version: int
    output_version: int | None
    decision_note: str | None         # 模型对本步动作的简短说明，不是完整推理过程
```

执行记录展示的是实际动作、结果和决策摘要，不代表模型内部的完整思维过程，也不能证明法律判断正确。

runs/agent.sqlite3 保存 LangGraph 消息与任务。agent_runs 保存预算、等待、最终结果及图检查点 ID。旧 checkpoint.json 在打开工作区时导入，原件保留。续用旧对话时迁移最后的图快照，历史运行仍可按原 ID 查询。缺少框架检查点时拒绝重放工具，不兼容的旧 engine 仍需新建会话。

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

| 项目 | 默认值 | `RunConfig` 字段 |
|---|---|---|
| 每次运行最多步数 | 30 | `max_steps` |
| 每次运行最多模型调用 | 不限（只受步数限制） | `max_model_calls` |
| 每次运行最多工具调用 | 20 | `max_tool_calls` |
| 每次运行 token 上限（输入加输出累计） | 200,000，试点后调整 | `max_total_tokens` |
| 每次运行执行时间（不含等待用户的时间） | 600 秒 | `max_run_seconds` |
| 单次模型调用输出 token 上限 | 不限（使用模型默认值） | `max_output_tokens` |
| 检索—核验循环 | 3 轮（与计划书一致） | `max_feedback_rounds` |
| 发给模型的上下文长度 | 60,000 字符 | `max_context_chars` |
| 单次工具超时 | 30 秒（工具可在 `ToolPolicy` 中另设） | `ToolPolicy.timeout_s` |
| 可重试错误的重试次数 | 1 次 | `ToolPolicy.max_retries` |

步数、工具调用、token 或执行时间达到上限时，运行进入 `budget_exhausted`，`FinalOutput.open_issues` 说明是哪项达到上限。

**用量统计边界**：`RunInfo.usage` 是一次运行的权威总计，已包含所有重试（`tool_attempts` 为含重试的工具执行次数）。评测方只读这个总计，不要再把执行记录里各步的用量相加，否则会重复计算。Runtime 负责以上运行内的上限；实验层面的上限（例如整批任务的总费用）由成员5负责。

**检索—核验循环**：同一 `feedback_loop` 中的工具每返回一次非 `ok` 结果（`empty`、`insufficient`、`error`），算作一轮。达到 3 轮后，Runtime 不再执行该循环中的工具，而是返回 `NOT_ALLOWED`，提示模型停止重试；最终输出的 `open_issues` 会自动加入一条说明。用户回复后轮数重新计算，因为新信息可能让检索重新有效。

**上下文压缩**：保存的完整历史不会被修改，只在发给模型时压缩。超过长度上限时，先把较早的工具结果缩减为 `status`、`summary` 和 `error`；仍然过长时，省略最早的几轮对话，并插入一条说明。用户的第一条消息和最近 4 轮始终完整保留；已确认的事实在每次调用时由案件状态提供，因此不依赖旧对话。

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
- 用户补充材料使 `case_version` 增加后，依赖旧版本的输出标记为 `stale`，Agent 在下一次运行中决定是否重新生成。目前 `get_run` 返回的 `RunInfo.stale` 表示最终结果所依据的案件版本已经过时；具体到每一份业务输出（证据目录、文书草稿）的过期标记，需要成员3的输出存储记录各自的 `case_version`。

---

## 7. 评测模式接口

成员5用同一入口批量运行四种模式。Direct LLM、RAG、Fixed Workflow 三个基线由成员5实现，复用同一 `BaseChatModel` 和已注册的工具；Agent 模式由成员4提供。

```python
class RunConfig(BaseModel):
    mode: Literal["direct_llm", "rag", "fixed_workflow", "agent"] = "agent"
    model: str | None = None                 # 传给每次模型调用；None 为客户端默认模型
    temperature: float = 0.0
    seed: int | None = None
    max_output_tokens: int | None = None
    max_steps: int = 30
    max_model_calls: int | None = None
    max_tool_calls: int = 20
    max_total_tokens: int | None = 200_000
    max_run_seconds: float | None = 600
    max_feedback_rounds: int = 3
    max_context_chars: int = 60_000
    enabled_tools: list[str] | None = None   # None 表示按阶段默认开放
```

组装一个可以直接运行的 Agent（真实模型、真实 `search_statutes`、成员3工具暂用 mock），用 `backend/agent/factory.py`：

```python
from backend.agent.factory import build_runner
from evaluation.config import run_config_from_pilot

cfg = run_config_from_pilot(json.loads(Path("evaluation/configs/pilot_v1.json").read_text()))
setup = build_runner(config=cfg)              # 读取 .env 中的 SILICONFLOW_API_KEY
setup.cases.create(case_id, {"facts": {...}}) # 每次重复用新的 case_id
result = AgentAdapter(setup.runner).run(case_id, user_message, cfg, exp_id, reply_to_wait)
```

`evaluation.config.run_config_from_pilot` 把评测计划中的模型、`max_output_tokens` 和 `budgets`（`max_model_calls`、`max_tool_calls`、`max_total_tokens`、`max_active_seconds`）对应到 `RunConfig`。模型不重试，无副作用工具按`ToolPolicy.max_retries`限制重试次数（默认最多1次），并受共同deadline约束。`build_runner(mock_business_tools=False)`保留真实检索和内存update_case，关闭模拟业务核验及文书工具。命令行试运行：`scripts/agent/run_agent.py`（`--dry-run --mock-statutes` 为全离线检查）。

Agent 模式的典型调用流程（由成员5的 adapter 实现，隐藏事实的披露也由成员5控制）：

```python
info = runner.start_run(case_id, user_message, config=cfg,
                        experiment_run_id=exp_id, request_id=f"{exp_id}:start")
while info.status == "waiting_user":
    reply = disclosure_controller.reply(info.wait)        # 只返回允许披露的事实
    info = runner.resume_run(info.run_id, reply, wait_id=info.wait.wait_id,
                             expected_case_version=info.case_version,
                             request_id=f"{exp_id}:{info.wait.wait_id}")
trace = runner.export_trace(info.run_id)                  # 分页读取，见第 4.1 节
manifest = runner.get_runtime_manifest()
```

- 测试集的标准答案和隐藏事实池不经过 Runtime，Agent 无法读取。
- 每次独立重复使用新的 `case_id`；继续运行时保持同一个 `run_id`。

### 7.1 与评测接口需求（成员5，Draft v0.1）的名称对照

成员5的需求文档允许各模块提供等价接口，由 `evaluation/adapters/` 做对应。以下名称保持本文档的写法：

| 评测文档中的名称 | 本文档 / 代码中的对应 |
|---|---|
| `generate(ModelRequest)` | `ModelAdapter.generate` → `BaseChatModel.bind(...).invoke(...)` |
| `generation.max_output_tokens` | `bind(max_tokens=)` / `RunConfig.max_output_tokens`；SDK可发送等价max_completion_tokens |
| `ToolCall.call_id` | `ToolCall.id` |
| 工具规格 `input_schema` | `ToolSpec.parameters` |
| `input_tokens` / `output_tokens` | `TokenUsage.prompt_tokens` / `completion_tokens` |
| `continue_run` | `resume_run` |
| `RunSnapshot` | `RunInfo` |
| `RunSnapshot.output` | `RunInfo.final` |
| `wait.questions[].question_id` | 相同 |
| `export_trace` 的事件 `sequence` | `TraceStep.step_id` |
| 运行状态 `queued` | 不使用（同步执行） |
| 调用结果信封 `{status, data, error, meta}` | 不使用；Python 调用直接返回结果或抛出异常（见第 8 节第 11 条） |

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
| 11 | 是否采用评测文档的调用结果信封 `{status, data, error, meta}` | 成员5 | Python 调用不使用信封，失败时抛出异常（`RunStateError`、`StaleWait`、`VersionConflict`、`RunConflict`）；HTTP 接口可由成员1按信封包装 |
| 12 | 错误码大小写 | 成员2、3、5 | 工具协议保持大写（`INVALID_ARGS` 等），由评测 adapter 转为小写 |
| 13 | 检索为空的状态 | 成员2、5 | 工具保持 `empty` 状态（Agent 据此改写查询并计入 3 轮上限），评测 adapter 视为成功的空结果 |
| 14 | 时间格式 | 全员 | 评测文档要求 UTC（`Z` 结尾），目前代码输出带时区的本地时间，待确认后统一 |

有意见请直接在 PR 中评论，或在群里说明章节号。确认后本文档版本改为 v1.0，之后的修改记录在下方。

## 修改记录

| 版本 | 日期 | 修改内容 |
|---|---|---|
| v0.1 | 2026-10-31 | 初稿 |
| v0.2 | 2026-11-01 | 按仓库目录框架补充工具代码分层（2.0）；运行记录改存 `var/runs/`；补充待确认问题 9、10 |
| v0.3 | 2026-11-01 | 与骨架代码对齐：新增错误码 `NOT_ALLOWED`、事件 `run.resumed` 和 `run.budget_exhausted`；运行接口返回 `RunInfo` 且为同步调用；`chat` 去掉 `run_id` 参数；补充模型环境变量和 `CaseStatePort` |
| v0.4 | 2026-11-01 | 新增 token 上限、检索—核验 3 轮上限（`ToolSpec.feedback_loop`）、上下文压缩和 `RunInfo.stale`（6.2、6.4） |
| v0.5 | 2026-11-03 | 按成员5的评测接口需求修改：`resume_run` 需要 `wait_id`，支持 `expected_case_version` 和 `request_id` 防重复；`pending` 改为 `Wait`（含 `material_request`）；新增 `export_trace`、`get_step_result`、`get_runtime_manifest`；执行记录增加 `event_id`、`parent_step_id`、`finished_at` 和完整结果文件；模型接口支持 `model`、`seed`、`tool_choice`，返回 `finish_reason`、请求 ID，用量未知时为 `null`，费用带币种和价格版本；`RunInfo` 增加 `case_version`、`experiment_run_id`；提前结束时保留已完成部分；`finish` 支持 `citations`；新增执行时间上限；新增与评测文档的名称对照（7.1）和待确认问题 11–14 |
| v0.6 | 2026-11-04 | `search_statutes` 接入真实检索（2.6）；去掉示例中的 `dispute_type` |
| v0.7 | 2026-11-10 | 新增 `RunConfig.max_model_calls`；第 7 节补充 `build_runner`、`run_config_from_pilot` 和命令行脚本 |
| v0.8 | 2026-11-10 | 模型层改用 LangChain（`LangChainLLMClient`，默认）；新增 `to_langchain_tools`；新增 `CIVRA_LLM_BACKEND` |
| v0.9 | 2026-11-10 | 迁移成员2适配：真实检索默认exact-vector，统一matches、结构化SourceRef、来源版本和快照记录；query/top_k为首轮参数，保留旧mock/检查点读取 |


| v1.0 | 2026-11-10 | 主循环迁移到create_agent；直接模型/工具入口，项目middleware保留预算、版本、顺序和轨迹；框架SQLite检查点，新旧未完成会话不跨版本恢复；同步评测调用并移除旧客户端及重复包装 |
| v1.1 | 2026-11-11 | 工具引用统一为matches[].source_ref，移除重复正文/引用和固定meta；Runtime派生轨迹与最终来源，mock复用工具合同，简化中间件结果处理 |
| v1.2 | 2026-11-11 | 删除SourceRef.version/excerpt及字符串定位兼容，只接受当前结构；运行记录格式升为v2，旧文件保持原样且不参与新运行加载 |
