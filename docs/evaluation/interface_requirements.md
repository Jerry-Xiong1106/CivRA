# 评测接口需求 / Evaluation interface requirements

**中文**

状态：成员5提出的草案v0.1，尚未与各接口负责人全部确认。第9节另列已经实现的本地映射。

**English**

Status: Draft v0.1 — proposed by Member 5; not yet agreed with interface owners.

## 1. Scope and ownership

**中文**

本清单说明评测运行器和三个基线需要其他模块提供什么。后文名称与字段是提议，不代表均已实现；负责人可提供等价接口，由成员5在`evaluation/adapters/`映射已确认差异。

离线实验优先使用可调用Python接口；签名是异步Python风格伪代码，不强制框架。独立服务可提供JSON HTTP接口，只需约定一种传输，不必两套都做。首次评测无需等待流式响应或前端。

接口先在此记录，负责人复核后再把公共定义放入`backend/common/`。成员5负责评测任务/结果结构，业务类型仍由对应负责人维护。

| 负责人 | 接口需求 | 首次联调目标 |
| --- | --- | --- |
| 成员2 | search_statutes、search_cases、get_source、get_knowledge_manifest | 有版本和原文定位的结果或明确空结果 |
| 成员3 | parse_documents、check_evidence、calculate_costs、compare_routes、draft_document、verify_output | 跑通一个试点所需业务工具 |
| 成员4 | generate、Agent运行生命周期、export_trace、get_runtime_manifest | Direct可运行，Agent可暂停追问并继续 |
| 成员1 | 必要时的案件/文件存取与产物读取 | 创建并读取隔离的评测案件和文件 |
| 成员5 | 任务加载、披露控制、基线、适配、评分和实验记录 | 同一任务接入各可用模式 |

本地存储等价时成员1不是前置依赖。评测不重复实现成员2–4的模块。

**English**

This checklist specifies what the evaluation runner and three baselines need from other modules. Names and fields below are proposed contracts, not existing implementations. Owners may offer equivalent interfaces; Member 5 will map agreed differences in `evaluation/adapters/`.

Prefer callable Python interfaces for offline experiments. Signatures below are asynchronous Python-style pseudocode, not a dependency or framework requirement. A JSON HTTP interface is acceptable when a module runs as a separate service. An implementation needs one agreed transport, not both. Streaming and frontend integration are not prerequisites for the first evaluation run.

Keep initial contracts in this document. Move accepted shared definitions into `backend/common/` only after owner review. Member 5 owns evaluation task/result schemas; domain types remain with their respective owners.

| Owner | Requested interfaces | First integration milestone |
| --- | --- | --- |
| Member 2 | `search_statutes`, `search_cases`, `get_source`, `get_knowledge_manifest` | A query returns locatable, versioned results or an explicit empty result |
| Member 3 | `parse_documents`, `check_evidence`, `calculate_costs`, `compare_routes`, `draft_document`, `verify_output` | One selected pilot task runs through its required business tools |
| Member 4 | `generate`, Agent run lifecycle, `export_trace`, `get_runtime_manifest` | Direct LLM runs; Agent can pause for clarification and continue |
| Member 1 | Case/file storage and output retrieval, if required by backend execution | One isolated evaluation case and its files can be created and read |
| Member 5 | Task loader, disclosure controller, baselines, adapters, scoring and experiment records | The same task can run through each available setting |

Member 1 is an optional integration dependency when local adapters can supply equivalent storage. Members 2–4 remain responsible for their module implementations; evaluation does not duplicate them.

## 2. Naming and wire conventions

**中文**

| 项目 | 建议约定 |
| --- | --- |
| 函数/JSON字段 | snake_case |
| 类型 | PascalCase |
| 标识 | 在声明范围内唯一的不透明字符串 |
| 引用 | case_id、file_id、source_id、run_id、step_id、request_id |
| 契约版本 | 顶层schema_version为0.1 |
| 日期 | YYYY-MM-DD，未知为null |
| 时间戳 | ISO 8601 UTC，如2026-11-02T12:00:00Z |
| 时长 | 非负整数毫秒，字段以_ms结尾 |
| 金额 | 十进制字符串并注明币种，不用二进制浮点 |
| 未知测量 | null，不用0或空字符串 |
| 集合 | 明确为空时用空数组 |
| 枚举 | 小写并记录允许值 |

除非注明可选，否则字段必填；可空字段未知时仍需出现。业务对象需提供带版本的字段定义和示例，仅有类型名不够。

**English**

| Item | Proposed convention |
| --- | --- |
| Functions and JSON fields | `snake_case` |
| Type names | `PascalCase` |
| Identifiers | Opaque strings, unique in their declared scope |
| References | `case_id`, `file_id`, `source_id`, `run_id`, `step_id`, `request_id` |
| Contract version | `schema_version: "0.1"` on top-level requests and results |
| Date | `YYYY-MM-DD`; unknown dates are `null` |
| Timestamp | ISO 8601 UTC, such as `2026-11-02T12:00:00Z` |
| Duration | Non-negative integer milliseconds, fields ending in `_ms` |
| Money | Decimal string with an explicit currency; never a binary floating-point amount |
| Unknown measurement | `null`, not zero or an empty string |
| Collections | Empty array when known to be empty |
| Enumerations | Lowercase strings; document allowed values |

Unless marked optional, listed fields are required. Nullable fields must still be present when the value is unknown. Owner-specific domain objects must have a versioned field definition and an example before integration; a type name alone is not a contract.

### Invocation context

**中文**

模型/工具调用的`InvocationContext`拟包含：

| 字段 | 类型 | 含义 |
| --- | --- | --- |
| schema_version | string | 契约版本 |
| request_id | string | 本次尝试唯一标识 |
| case_id | string或null | 隔离案件；仅创建前可为null |
| experiment_run_id | string | 单个模式/任务/重复的实验记录 |
| deadline_at | string或null | 绝对截止时间 |

Agent另有`run_id`，需关联实验ID。恢复保留运行ID，独立重复创建新案件和实验记录。这些标识仅用于关联，不是鉴权凭据。

**English**

Each model/tool invocation accepts an `InvocationContext` containing:

| Field | Type | Meaning |
| --- | --- | --- |
| `schema_version` | `string` | Contract version |
| `request_id` | `string` | Unique identifier for this invocation attempt |
| `case_id` | `string \| null` | Isolated evaluation case; `null` only before case creation |
| `experiment_run_id` | `string` | Member 5's record for one setting/task/repetition |
| `deadline_at` | `string \| null` | Absolute execution deadline |

Agent execution also has its own `run_id`. Preserve its association with `experiment_run_id`. A resumed Agent keeps its `run_id`; an independent repetition receives a new case and experiment record. Correlation identifiers are not authorization credentials.

### Invocation result

**中文**

草案建议普通模型/工具/存储调用使用下方JSON信封：`data`是接口专属结果，成功时`error=null`；空检索也是成功。Agent生命周期放在data内，与本次调用是否成功分开。示例字段保持英文。

`status`为ok或error；错误时data为null，error含字符串code/message、布尔retryable和可空对象details。建议错误码：invalid_request、not_found、version_conflict、timeout、rate_limited、provider_error、tool_error、cancelled。

负责人说明内部重试并暴露次数，成员5不应无意再加第二层重试。日志不得含密钥或隐藏标注。实际Python接口目前通过返回对象/异常适配，见第9节，不强行套用本提议信封。

**English**

Ordinary model/tool/storage calls return this envelope. `data` is the interface-specific payload; `error` is `null` on success. An empty search is a successful call with no matches. Agent lifecycle state is carried inside `data`, separate from invocation success.

```json
{
  "schema_version": "0.1",
  "request_id": "request-001",
  "status": "ok",
  "data": {},
  "error": null,
  "meta": {
    "duration_ms": 85,
    "implementation_version": "owner-supplied-version"
  }
}
```

`status` is `ok` or `error`. For errors, `data` is `null` and `error` contains `code: string`, `message: string`, `retryable: boolean`, and `details: object | null`. Proposed codes include `invalid_request`, `not_found`, `version_conflict`, `timeout`, `rate_limited`, `provider_error`, `tool_error`, and `cancelled`.

Owners document internal retries and expose attempt counts. Member 5 must not unknowingly add a second retry loop. Logs must not contain credentials or hidden evaluation annotations.

## 3. Member 2: retrieval and sources

**中文**

成员2负责检索与来源。本节分述函数、来源结构和联调样例，均以确认后的实际接口为准。

**English**

Member 2 owns retrieval and sources. The following sections specify functions, source structures, and integration fixtures; confirmed implementations take precedence.

### Interfaces

**中文**

| 函数 | 上下文之外的输入 | 输出 |
| --- | --- | --- |
| search_statutes(StatuteSearchRequest) | query字符串、SearchFilters、top_k整数、knowledge_version | matches数组、可空empty_reason、知识版本 |
| search_cases(CaseSearchRequest) | 相同公共字段及负责人定义的类案筛选 | 同一检索结果格式 |
| get_source(SourceRequest) | source_id、source_version、可空SourceLocator | 原文/许可片段、定位与SourceRef |
| get_knowledge_manifest() | 无 | 快照、构建/索引版本、覆盖和排除清单引用 |

SearchFilters拟支持可空domain、jurisdiction、incident_date和as_of_date；类案可加裁判日期范围。逐项说明语义，不支持的筛选应报错，不能忽略。查询可中文，字段为英文。

SearchMatch含text、SourceRef、rank及可空数值score。说明分数含义，不假设不同方法分数可比。

**English**

| Signature | Request fields beyond context | Result payload |
| --- | --- | --- |
| `search_statutes(request: StatuteSearchRequest) -> SearchResult` | `query: string`, `filters: SearchFilters`, `top_k: integer`, `knowledge_version: string` | `matches: SearchMatch[]`, `empty_reason: string \| null`, `knowledge_version: string` |
| `search_cases(request: CaseSearchRequest) -> SearchResult` | Same common search fields; case-specific filters documented by owner | Same search result format |
| `get_source(request: SourceRequest) -> SourceRecord` | `source_id: string`, `source_version: string`, `locator: SourceLocator \| null` | Original text or permitted excerpt, resolved location, and `SourceRef` |
| `get_knowledge_manifest() -> KnowledgeManifest` | None | Snapshot identity, build/index versions, coverage, and exclusion-manifest reference |

`SearchFilters` supports nullable `domain`, `jurisdiction`, `incident_date`, and `as_of_date`; case searches may also use a decision-date range. Document each filter's meaning. Reject unsupported filters rather than silently ignoring them. Queries may be Chinese; field names remain English.

`SearchMatch` contains `text: string`, `source_ref: SourceRef`, `rank: integer`, and `score: number | null`. Document score meaning; scores are not assumed comparable across search methods.

### Source reference

**中文**

SourceRef由成员2维护，拟包含：source_id/source_type/title/source_version字符串；可空原始URL；SourceLocator中的article/page/paragraph/chunk_id至少一个有值；适用时记录可空生效起止和裁判日期；retrieved_at时间。

get_source必须解析检索时的同一版本，不能悄悄换最新原文；无法获取时明确报错或说明访问限制。

**English**

Member 2 owns `SourceRef`. Proposed fields:

- `source_id`, `source_type`, `title`, `source_version`: strings identifying the source and version.
- `url: string | null`: original source URL, if available.
- `locator: SourceLocator`: applicable `article`, `page`, `paragraph`, or `chunk_id` fields; at least one populated.
- `effective_from`, `effective_to`, `decision_date`: nullable dates, populated where applicable.
- `retrieved_at`: timestamp.

`get_source` must resolve the same version returned by retrieval, not silently substitute the newest text. If access is unavailable, return an explicit error or documented access limitation.

### Needed for evaluation

**中文**

- 提供一组有命中、一组无命中的请求/回复。
- 提供可复现知识快照或含语料/索引版本的清单。
- 提供排除最终测试来源裁判及相关答案材料的验证方式；成员5给受控案件族清单，只记录版本/摘要，不向检索泄漏答案。
- 支持试点依据及检索相关性标注。

**English**

- One query/result fixture and one no-match fixture.
- A reproducible knowledge snapshot or manifest with corpus/index versions.
- A way to verify that final-test source judgments and related answer-revealing cases are excluded. Member 5 supplies the controlled case-family exclusion list; record its version or digest without exposing gold answers to retrieval.
- Relevant-authority and retrieval-relevance annotations for pilot review.

## 4. Member 3: case and business tools

**中文**

依赖案件状态的请求包含上下文和整数`case_version`，结果说明使用的版本。业务类型由成员3定义，下表只列必要信息。

| 工具 | 必要输入 | 必要输出 |
| --- | --- | --- |
| parse_documents | file_ids | 文本/字段、文件页码片段定位、解析/OCR警告 |
| check_evidence | CaseSnapshot | 缺失、冲突、重复、材料引用、追问建议 |
| calculate_costs | 版本化计算类型、inputs、rule_refs | 金额币种、公式、输入、假设、依据及未解决输入 |
| compare_routes | 快照、依据、计算结果、用户目标 | 路线、前提、受支持的费用/步骤、不确定性与引用 |
| draft_document | 确认事实、路线、模板ID/版本、引用 | 文本或artifact_id、缺字段、事实证据关联和模板版本 |
| verify_output | 待核验输出、快照、引用原文 | 严重程度、问题位置、依据和修正建议 |

CaseSnapshot拟含case_id、case_version、facts、evidence、claims及可空selected_route；区分主张和确认事实，证据带材料ID及定位，具体定义由成员3确认。

路线标识拟为negotiation/mediation/litigation。未知值明确保留。工具只返回发现，不直接暂停或替用户选路线；成员4控制Agent，成员5控制固定基线。

如暴露update_case，应接收expected_case_version，返回新版本并报告旧版本冲突。成员3定义变更，成员1可负责持久化；评测不另建竞争案件库。

**English**

All requests include context and the relevant `case_version: integer` when they depend on case state. All results identify the version used. Member 3 owns the domain schemas; this table lists required information, not complete business algorithms.

| Interface | Required input | Required output information |
| --- | --- | --- |
| `parse_documents` | `file_ids: string[]` | Extracted text/fields, file/page/span references, extraction or OCR warnings |
| `check_evidence` | `case_snapshot: CaseSnapshot` | Missing facts, conflicts, duplicates, material references, and suggested questions |
| `calculate_costs` | Versioned calculation type, `inputs`, `rule_refs: SourceRef[]` | Decimal amounts and currency, formula, inputs, assumptions, rule references, unresolved inputs |
| `compare_routes` | Snapshot, available authorities, calculation results, user goals | Route options, prerequisites, supported costs/steps, uncertainties, and supporting references |
| `draft_document` | Confirmed facts, route, `template_id`, `template_version`, source references | Text or `artifact_id`, missing fields, fact/evidence links, and template/version used |
| `verify_output` | Candidate output, snapshot, cited sources | Findings by severity, affected output locations, supporting references, and suggested corrections |

`CaseSnapshot` needs at least `case_id`, `case_version`, `facts`, `evidence`, `claims`, and nullable `selected_route`. Facts distinguish claims from confirmed information. Evidence records contain material identifiers and source locations. Exact field definitions require Member 3's approval.

Use proposed route identifiers `negotiation`, `mediation`, and `litigation`. Unknown values stay explicit. Tool findings are data; they do not themselves pause execution or select the user's route. Member 4 controls Agent decisions; Member 5 controls the fixed baseline sequence.

If `update_case` is exposed, it must accept `expected_case_version`, return the new version, and report stale-write conflicts. Member 3 defines the state change; Member 1 may persist it. Do not implement independent competing case stores in evaluation.

### Needed for evaluation

**中文**

- 优先交付首个试点需要的工具，不要求全部工具先完成。
- 每个接入工具提供有效输入输出及无效输入样例。
- 说明计算和已知限制，以便核对预期结果。
- 复核试点事实、证据、文书要求和可接受输出；工具自己的核验结果不是独立gold。

**English**

- Prioritize tools required by the first pilot task; not every tool is an initial blocker.
- Provide one valid request/result fixture per integrated tool and an invalid-input fixture.
- Document calculations and known limitations so expected results can be checked.
- Review pilot facts, evidence issues, document requirements, and acceptable outputs. A tool's own verification result is not an independent gold label.

## 5. Member 4: model access and Agent execution

**中文**

成员4提供共享模型和Agent执行。下文为需求定义，实际名称由适配层映射；已完成的真实与模拟验证见第9节。

**English**

Member 4 provides shared model access and Agent execution. The following names are interface proposals mapped by adapters; section 9 records completed real and scripted checks.

### Shared model interface

**中文**

拟议入口为generate(ModelRequest)返回ModelResponse。输入包含：模型标识、有序messages、明确生成参数及输出上限/可空seed、tools（Direct为空）、工具选择规则或明确不支持的回复。

消息含role（system/user/assistant/tool）、可空content、assistant的tool_calls和工具回复的tool_call_id。调用含call_id、tool_name、解析后的JSON对象arguments；工具规格含name、description和input_schema。执行前校验参数，适配层说明供应商限制。

响应需content、tool_calls、finish_reason、解析后的模型身份、可取得的供应商请求ID及用量费用；仅工具调用时文本可空。

用量含可空input_tokens/output_tokens/total_tokens、attempt_count，以及供应商支持的缓存/推理统计；说明重叠计数，避免重复。费用包括金额、币种、实际/估算依据和价格版本，未知为null。

不支持的参数应声明，不应在各模式间暗改。基线和Agent共用该接口，固定流程可多次调用。

**English**

`generate(request: ModelRequest) -> ModelResponse`

| Request field beyond context | Type / requirement |
| --- | --- |
| `model` | Model identifier |
| `messages` | Ordered message objects |
| `generation` | Explicit parameters, including output-token limit; nullable seed where supported |
| `tools` | Tool specifications; empty for Direct LLM |
| `tool_choice` | Agreed provider-neutral selection value or explicit unsupported response |

Messages need `role` (`system`, `user`, `assistant`, `tool`), nullable `content`, assistant `tool_calls`, and tool-message `tool_call_id`. Each tool call contains `call_id`, `tool_name`, and parsed JSON-object `arguments`. Each tool specification contains `name`, `description`, and an `input_schema` JSON Schema. Validate arguments before execution. Document provider-specific constraints in the adapter.

The response needs `content: string | null`, `tool_calls: ToolCall[]`, `finish_reason: string`, resolved model identity, provider request identifier if available, and usage/cost metadata. A tool-call response may have no text.

Usage fields are nullable `input_tokens`, `output_tokens`, `total_tokens`, plus `attempt_count` and any separate cache/reasoning counts supported by the provider. Document overlapping counts to avoid double-counting. Cost metadata identifies amount, currency, billed/estimated basis, and price version, or is `null`.

Unsupported generation options must be declared; do not silently change parameters between settings. The same interface serves the baselines and Agent, including repeated model calls in Fixed Workflow.

### Agent lifecycle

**中文**

| 接口 | 输入 | 返回 |
| --- | --- | --- |
| start_run | 上下文、公开快照/材料、消息、配置、预算 | RunSnapshot |
| get_run | run_id | 当前快照 |
| continue_run | run_id、request_id、expected_case_version、wait_id、回复或路线 | 更新快照 |
| cancel_run | run_id、request_id | 确认及状态，查询至终态 |
| export_trace | run_id、可选游标 | 有序事件、下一游标、完整标记 |
| get_runtime_manifest | 无 | Runtime、提示词、工具Schema和模型配置版本 |

拟议状态queued/running/waiting_user/completed/failed/cancelled/budget_exhausted；最后4项为终态。快照包含运行/案件ID和版本、状态、可空wait/output/error、用量。约定调用同步等待或立即返回，并支持截止时间。下方JSON展示等待结构。

其他等待类型为route_selection和material_request。成员5控制器读取隐藏事实，仅回复允许内容；Agent请求不含gold或完整事实池。路线依照预定用户偏好，不由评测者临时编造。

继续运行须拒绝过期wait_id/版本，防止重复执行。说明请求ID去重与客户端超时恢复。独立重复隔离状态，恢复保留原run_id。

**English**

| Interface | Required request | Result |
| --- | --- | --- |
| `start_run` | Context, public case snapshot/material references, user message, runtime config, budgets | `RunSnapshot` |
| `get_run` | `run_id` | Current `RunSnapshot` |
| `continue_run` | `run_id`, `request_id`, `expected_case_version`, `wait_id`, user reply or selected route | Updated `RunSnapshot` |
| `cancel_run` | `run_id`, `request_id` | Acknowledgment and current state; poll until terminal |
| `export_trace` | `run_id`, optional cursor | Ordered events, nullable next cursor, and completeness indicator |
| `get_runtime_manifest` | No request parameters | Runtime, prompt, tool-schema, and model configuration versions |

Proposed lifecycle states: `queued`, `running`, `waiting_user`, `completed`, `failed`, `cancelled`, `budget_exhausted`. The last four are terminal. `RunSnapshot` contains `run_id`, `case_id`, `case_version`, `status`, nullable `wait`, nullable `output`, nullable `error`, and usage summary. Define whether lifecycle methods return immediately or wait; both must support an execution deadline.

When waiting, expose a structured request:

```json
{
  "run_id": "agent-run-001",
  "case_id": "eval-case-001",
  "case_version": 1,
  "status": "waiting_user",
  "wait": {
    "wait_id": "wait-001",
    "kind": "clarification",
    "questions": [
      {"question_id": "question-001", "text": "Has any amount been repaid?"}
    ],
    "options": []
  },
  "output": null,
  "error": null
}
```

Other proposed wait kinds are `route_selection` and `material_request`. The disclosure controller in Member 5's runner reads hidden facts and returns only permitted replies. Agent requests never include gold annotations or the hidden-fact pool. For route selection, follow the task's predefined user preference; do not let the evaluator improvise it.

`continue_run` must reject stale wait identifiers or versions without duplicate execution. Document how request identifiers prevent duplicate starts/resumes and how to recover after a client timeout. Each repetition starts with isolated state; resumes retain the same Agent run.

### Outputs and traces

**中文**

最终或部分输出含回答、与位置关联的SourceRef、产物、未解决事项、案件/输出版本及完成原因。失败或预算耗尽时尽可能保留部分成果，且无需前端即可读取。

轨迹拟含event_id、运行内递增sequence、run_id、step_id、可选父步骤、类型、模型/工具名、状态、时间、耗时、输入输出引用、来源、版本、错误及用量。通过受控访问读取完整输入与结果，让评测核查参数和反馈。

记录真实调用、重试、等待、错误及动作摘要，不导出私有完整思维过程。注明权威总计和是否含重试，不能同时累加事件及总计。

Runtime管理运行内预算，成员5管理实验级预算；确认统计边界，避免费用/限制重复计算。

**English**

Final or partial outputs need answer text, cited `SourceRef` objects linked to output locations, artifact references, unresolved items, case/output versions, and a completion reason. Retain partial output on failure or budget exhaustion when available. Outputs must be readable without the frontend.

Trace events need `event_id`, monotonic per-run `sequence`, `run_id`, `step_id`, optional parent step, event type, model/tool name as applicable, status, timestamps, duration, input/output references, source references, versions, error, and usage where applicable. Export referenced inputs/results through the same controlled access path so Member 5 can inspect tool arguments and feedback.

Record actual calls, retries, waits, and errors. Export observable actions and decision summaries, not private model reasoning. Identify authoritative usage totals and whether summaries include retries; evaluation must not sum the same calls from both events and summaries.

Runtime enforces internal call/token/time budgets; Member 5 enforces experiment-level limits. Agree on accounting boundaries so limits and costs are not counted twice.

## 6. Member 1: optional storage/service integration

**中文**

仅在Agent或工具要求应用管理案件/文件时依赖成员1；本地等价存储需保留同样标识和隔离。

| 接口 | 输入 | 输出 |
| --- | --- | --- |
| create_case | 上下文和公开初始数据 | case_id、case_version |
| upload_file | 案件ID、文件名、媒体类型、字节/流 | 文件ID、校验和、大小、案件关联 |
| get_case | 案件ID、可选版本 | 版本化公开快照 |
| get_file | 案件ID、文件ID | 内容/流和媒体类型 |
| get_artifact | 案件ID、产物ID | 内容/流、媒体类型、版本 |

HTTP建议使用下方英文段落的/api/v1路径。上传可用multipart，下载用二进制；普通JSON采用约定信封。需说明鉴权、请求限制及HTTP状态映射，路径由成员1确认。

评测案件彼此隔离，不上传gold。凭据放本地环境，不放任务JSON或轨迹。即使HTTP由成员1暴露，生命周期与轨迹仍由成员4维护。

**English**

Use these only if Agent/tools require application-managed cases or files. Equivalent local storage is acceptable if it preserves the same identifiers and isolation.

| Callable name | Request | Result |
| --- | --- | --- |
| `create_case` | Context, public initial case data | `case_id`, `case_version` |
| `upload_file` | `case_id`, filename, media type, bytes/stream | `file_id`, checksum, size, case association |
| `get_case` | `case_id`, optional version | Versioned public snapshot |
| `get_file` | `case_id`, `file_id` | File content/stream and media type |
| `get_artifact` | `case_id`, `artifact_id` | Output content/stream, media type, and version |

For HTTP transport, proposed routes are `POST /api/v1/cases`, `POST /api/v1/cases/{case_id}/files`, `GET /api/v1/cases/{case_id}`, `GET /api/v1/cases/{case_id}/files/{file_id}`, and `GET /api/v1/cases/{case_id}/artifacts/{artifact_id}`. Use multipart uploads and binary downloads where appropriate; ordinary JSON responses use the agreed envelope. Document authentication, request limits, and status-code mappings. These route names require Member 1's confirmation.

Keep evaluation data isolated from other cases. Never upload gold annotations with public materials. Store credentials in local environment configuration, not task JSON or traces. Agent lifecycle and trace ownership remain with Member 4 even if Member 1 exposes HTTP routes.

## 7. Fixtures and acceptance checks

**中文**

交付可调用入口或服务地址、配置说明、契约版本、限制和合成样例；样例不含个人信息或密钥。

| 检查 | 预期 | 负责人/复核 |
| --- | --- | --- |
| 引用解析 | 同版本原文可定位 | 成员2/5 |
| 无命中 | 成功的空结果，不是工具错误 | 成员2/5 |
| 证据缺失/冲突 | 指向具体事实和材料位置 | 成员3/5 |
| 工具调用往返 | 调用ID和JSON参数可完整传递 | 成员4/5 |
| 澄清恢复 | 只披露允许事实，同一次运行继续 | 成员4、5 |
| 超时重试 | 失败和全部尝试可见，无重复执行 | 接口负责人/5 |
| 轨迹统计 | 用量、重试、完整结果可审计且不重复 | 成员4/5 |
| 案件隔离 | 重复实验不互读状态/材料 | 成员1、4、5 |
| 版本冲突 | 旧更新/恢复被明确拒绝 | 相关成员1、3、4/5 |

**English**

Owners should deliver a callable entry point or service address, setup/configuration instructions, contract version, limitations, and synthetic request/result fixtures. Fixtures must contain no personal data or secrets.

| Check | Expected result | Owner / reviewer |
| --- | --- | --- |
| Citation resolution | A retrieved reference resolves to the recorded text/version | Member 2 / Member 5 |
| No retrieval matches | Successful empty result, not a tool failure | Member 2 / Member 5 |
| Missing/conflicting evidence | Findings identify affected facts and material locations | Member 3 / Member 5 |
| Model tool-call exchange | Tool-call identifiers and JSON arguments survive a request/response cycle | Member 4 / Member 5 |
| Clarification and resume | Runner discloses only permitted facts; Agent continues the same run | Members 4 and 5 |
| Timeout/retry | Failure and all attempts are visible without duplicate work | Interface owner / Member 5 |
| Trace accounting | Usage, retries, and referenced results can be inspected without double-counting | Member 4 / Member 5 |
| Case isolation | Independent repetitions cannot read each other's materials/state | Members 1, 4 and 5 |
| Version conflict | Stale updates/resumes are rejected explicitly | Members 1, 3, 4 as applicable / Member 5 |

## 8. Delivery order and decisions

**中文**

1. 成员5按草案定义任务/结果和模拟能力。
2. 成员2–4确认名称、传输、最小字段及样例；必要时成员1确认存储。
3. 先接共享模型跑Direct，再接检索跑RAG。
4. 接试点业务工具跑Fixed Workflow。
5. 接Agent启动、等待、恢复、输出、轨迹完成四模式试点。
6. 正式冻结前解决Schema、泄漏、重试/预算和版本问题。

待决事项包括真实导入路径/服务URL、同步或异步、字段归属、支持参数/筛选、预算、产物保存、轨迹访问和契约变更策略。确认前在此保留负责人回复，不能视为已同意。

相关文档见下方英文区块链接。

**English**

1. Member 5 drafts task/result schemas and mocks against these proposed contracts.
2. Members 2–4 confirm names, transports, minimum fields, and one fixture each. Member 1 confirms storage only if required.
3. Integrate shared model access for Direct LLM, then retrieval for RAG.
4. Integrate pilot-required business tools for Fixed Workflow.
5. Integrate Agent start/wait/resume/output/trace for the four-setting pilot.
6. Resolve schema gaps, leakage checks, retry/budget accounting, and versioning before freezing formal experiments.

Open decisions: actual module import paths or service URLs; synchronous versus asynchronous implementation; field/schema ownership details; supported filters/model options; budget values; artifact retention; trace access; and contract-change policy. Record owner responses here before treating any proposed interface as agreed.

Related documents: [protocol](protocol.md), [annotation guide](annotation_guide.md), [rubric](rubric.md), and [module README](../../evaluation/README.md).

## 9. Local pilot mappings

**中文**

已在[team.py](../../evaluation/adapters/team.py)实现：

| 评测入口 | 实际接口及映射 | 验证范围 |
| --- | --- | --- |
| ModelAdapter.generate | BaseChatModel.invoke；输出上限映射max_tokens，prompt/completion映射input/output | 新接口已做本地HTTP/脚本验证；既有10次真实结果属于迁移前实现 |
| RetrievalAdapter.search_statutes | RetrievalService.search；复用to_search_match，正常no_results映射成功空matches，引用/快照与真实工具一致 | 新结构已离线验证；既有5次真实向量结果使用迁移前载荷 |
| AgentAdapter.run | start_run/resume_run、UserReply、wait/version/request保护、export_trace | 仅脚本暂停/恢复/轨迹测试 |

Agent披露由回调决定，尚无自动语义隐藏事实控制器。基线只加载公开任务，私有标注由独立评分命令读取。工具调用ID、参数Schema和来源归属复用团队类型，不重复实现检索算法或案件数据库。

**English**

Implemented in [team.py](../../evaluation/adapters/team.py):

| Evaluation API | Existing team API | Verification |
| --- | --- | --- |
| ModelAdapter.generate | BaseChatModel.invoke; max_output_tokens becomes max_tokens; prompt/completion tokens become input/output tokens | New interface verified with local HTTP/scripted tests; the ten retained real generations predate this migration |
| RetrievalAdapter.search_statutes | RetrievalService.search plus shared to_search_match; normal no_results becomes empty matches; structured references and snapshot identity match the real tool | New structure verified offline; the five retained real queries use the earlier payload |
| AgentAdapter.run | AgentRunner.start_run/resume_run, UserReply, wait_id/version/request safeguards, export_trace | Scripted pause/resume/trace test only |

The Agent adapter delegates disclosure decisions to a callback. It is not an
automatic semantic hidden-fact controller. Baseline execution loads public tasks
only; private annotations are read by the separate review command. Tool-call IDs,
tool parameter schemas, and source ownership remain with the shared team types.
No duplicate retrieval algorithm or application case database is introduced.
