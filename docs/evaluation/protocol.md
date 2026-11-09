# 试点实验协议 / Pilot evaluation protocol

**中文**

版本：pilot-v0.3；负责人：成员5。状态：本地开发计划，尚非最终测试冻结版或团队已确认契约。

**English**

Version: pilot-v0.3. Owner: Member 5. Status: local development plan, not a final-test freeze or team-approved contract.

## Immediate scope

**中文**

试点覆盖咨询、事实澄清、引用和依据不足。现有5题：完整借贷事实、缺失还款事实、语料缺少官方统计、未约定利息和超期租赁；4静态、1交互设计。后两题改写自既有检索开发题，不是未见案例。

首轮比较Direct LLM与RAG，两者已实现并完成真实5题运行。Fixed Workflow和Agent效果实验仍待进行；只报告真正实现并执行过的模式。Agent适配只通过脚本回复测试。

**English**

Prepare a small, inspectable experiment for consultation, fact clarification, citation handling, and source insufficiency. The dataset has five development tasks: complete lending facts, missing repayment facts, a requested statistic absent from the current legal corpus, no-agreed-interest consultation, and an over-limit lease term. It contains four static tasks and one interactive task. The last two are explicitly adapted from existing retrieval development questions, not unseen cases.

The first executable comparison will be Direct LLM versus RAG. Fixed Workflow and Agent remain planned settings. A mode is reported only after its implementation and dependencies actually run. Direct LLM and RAG are implemented and have completed the first real five-task run. Fixed Workflow and Agent effectiveness remain pending. The Agent adapter has been tested with scripted responses only.

## Settings

**中文**

| 模式 | 本试点执行规则 |
| --- | --- |
| Direct LLM | 同一公开输入，生成一次，不检索、不调用工具、不接收控制器回复 |
| RAG | 原样使用`initial_input`检索一次，取前5个候选，与同一公开输入一起生成一次；不动态调用工具或接收回复 |
| Fixed Workflow | 下一阶段按固定次序检查证据、至多一批固定澄清、更新、检索、生成、核验；报告效果前需真实业务工具 |
| Agent | 使用成员4的AgentRunner和同一模型/检索快照，在预算内动态行动，经控制器处理等待 |

模型输入仅由`PublicTask.to_model_input()`生成，不能序列化控制器或评分记录；各模式初始输出要求一致。任务正文为中文，代码字段为英文，说明文档提供双语。

**English**

| Setting | Frozen pilot procedure |
| --- | --- |
| Direct LLM | One generation using the public case packet; no retrieval, tools, or controller replies |
| RAG | Use initial_input unchanged as one query; retrieve top five candidates once; generate once from the same public packet plus retrieved text and source identifiers; no dynamic tools or controller replies |
| Fixed Workflow | Next-stage procedure: check evidence, ask at most one scripted clarification batch if indicated, update state, retrieve once, generate, then verify once. No model-selected action ordering. Requires real business tools before effectiveness reporting |
| Agent | Use Member 4's AgentRunner with the same model and retrieval snapshot; dynamic actions within declared budgets; handle waits through the disclosure controller |

The public packet is produced only by PublicTask.to_model_input(). Never serialize controller or scoring records into a model request. Initial output requirements are identical across settings. Task content is Chinese; implementation and field names use English, and documentation is bilingual.

## Shared configuration

**中文**

配置保存于[pilot_v1.json](../../evaluation/configs/pilot_v1.json)，目前仍属试点暂定值：

- 供应商硅基流动；前两种模式采用鉴权列表确认可用的`Qwen/Qwen2.5-7B-Instruct`。真实工具调用兼容性未验证；后续沿用同模型，更换时整体实验需另记版本。
- 温度0，seed不设置，重复1次，每次输出最多2000 Token。
- exact-vector、metadata输入、top_k=5；检索模式共用语料/索引快照。
- 通用上限：12次模型、8次工具、24000模型Token、300秒活跃时间、3轮用户交互；前两基线还受更严格的单次流程限制。
- 模型不重试；可重试工具失败至多重试1次并留痕。
- 未配置价格、币种和价格版本前，费用为未知。

不能让Agent悄悄使用关键词而RAG使用向量。关键词冒烟应另建配置并对所有检索模式一致使用；检索通道消融与四模式比较分开。

用户已授权本地付费执行；模型为空或`paid_execution_enabled=false`时命令拒绝运行。密钥只通过环境变量提供，不能放在任务或配置中。首次批次保存10次真实生成。

**English**

The machine-readable plan is [pilot_v1.json](../../evaluation/configs/pilot_v1.json). Its values are provisional pilot decisions:

- Provider: SiliconFlow. The first two baselines use Qwen/Qwen2.5-7B-Instruct, confirmed in the authenticated model list. Tool-calling compatibility is not yet verified; use the same selected model for future comparisons or version the entire experiment when changing it.
- Temperature: 0.0; seed: unset; repetitions: one; output limit: 2,000 tokens per model call.
- Retrieval: exact-vector, metadata input, top_k=5, using the same corpus/index snapshot for all retrieval-enabled settings.
- General run caps: 12 model calls, 8 tool calls, 24,000 total model tokens, 300 active seconds, 3 user-interaction turns.
- Model retries: zero; retryable tool failures: at most one retry, logged. Direct LLM and RAG have the stricter procedure-specific limits above.
- Costs remain unknown until prices, currency, and price-table version are configured.

Do not silently switch the Agent to keyword retrieval while RAG uses vectors. If a keyword-only smoke run is needed, record a separate configuration and use it for every retrieval-enabled setting in that comparison. Keep retrieval-channel ablations separate from the four-setting comparison.

Paid execution is enabled in this development plan following user authorization. The CLI refuses execution while the model is unset or paid_execution_enabled is false. Credentials are supplied through the existing runtime environment variables; they never belong in task/configuration files. The recorded first batch contains ten real generations, not mock outputs.

## Inputs and disclosure

**中文**

静态任务不接收控制器回复。交互任务共享初始输入与隐藏事实池，但实际披露可不同。Direct和RAG保持单轮，可用明确条件结论满足不确定性项；它们与静态任务分开报告，不能声称最终信息相同。

小规模试点先由评审按语义匹配披露条件与示例，记录问题、决定、事实ID、回复、wait_id和时间。模糊请求裁决前不披露；未匹配使用固定的无更多信息回复；不一次性透露整个事实池；重复问题一致回答。路线只能采用预设偏好，没有偏好时以未解决事项结束。

本阶段未实现交互控制器。记录格式通过不代表语义匹配已测。

**English**

Static tasks never receive controller replies. Interactive tasks share one initial packet and one controlled hidden-fact pool; actual disclosures may differ by setting. Direct LLM and RAG remain noninteractive and may satisfy uncertainty items with explicit conditional conclusions. Report these tasks separately from static tasks; do not describe their final information as identical.

For this small pilot, a reviewer matches questions semantically against the recorded disclosure condition and examples. Log the question, decision, fact identifier, reply, wait_id, and timestamp. Ambiguous requests receive no disclosure until reviewer adjudication. Unmatched requests receive the plan's fixed unavailable-information reply. Never automatically reveal an entire hidden pool. Repeated questions receive consistent answers. Use only a predefined route choice; stop with an unresolved item if none exists.

The interaction controller is not implemented in this stage. Validation of disclosure records does not imply that semantic matching has been tested.

## Data and review

**中文**

公开任务在`evaluation/datasets/pilot/tasks.jsonl`；隐藏事实和gold在被Git忽略的本地`evaluation/private/pilot_v1/`。完整克隆仅有公开题，关联校验需受控标注；用约定渠道传递，不能放入检索索引。

5题均为开发题，前三题合成、后两题来自已见问题。Gold为行为/算术/法律规则草稿；后两题引用源于既有语料标注，不是独立法律核验。成员2、3复核前不能声称法律准确率；依据列表为空不等于无需依据。

现有检索报告属于回归证据，不是新端到端盲测。原案及改写放在同一划分；校验器只检查所给清单，正式准备需合并校验完整划分清单。

**English**

Public tasks are in evaluation/datasets/pilot/tasks.jsonl. Hidden facts and gold annotations are local-only under evaluation/private/pilot_v1/ and ignored by Git. A fresh checkout contains the public examples but needs controlled annotation files for full bundle validation. Transfer those files through an agreed controlled channel, not retrieval indexes.

All five cases are development-only; the first three are synthetic and the last two derive from seen retrieval questions. Gold annotations remain draft behavioral/arithmetic and legal-rule checks. Authority references are populated for the last two from existing corpus annotations, not independently approved legal opinions. Members 2 and 3 must review authorities and substantive conclusions before legal-accuracy claims. Empty authority lists do not mean no authority is required.

Existing retrieval development and reviewed-query reports remain regression evidence. They are not new unseen end-to-end tests. Keep original cases and derivatives in one split. The bundle validator checks this within the submitted manifest; formal preparation must validate the entire split manifest together.

## Output, scoring, and accounting

**中文**

运行记录应包含任务/实验ID、模式、重复编号、公开输入哈希、代码版本、模型/提示词版本、检索配置、语料/索引哈希、输出、披露、错误、用量、耗时与轨迹引用。独立重复使用新案件ID。

按受控gold的0–2分逐项评价：先在任务内按质量维度取平均，再跨任务平均；所有必需成功项都需2分。行为项只对列明模式适用并单独报告；保留理由，不以逐字匹配判分。

Agent读取`RunInfo.usage`，不能再叠加步骤统计。`tokens_complete=false`代表统计不完整，未知费用为null；基线记录每次尝试。尽可能分开活跃运行、检索/嵌入、模型及评审/用户等待时间，并说明索引构建是否排除。

保留失败和部分输出，必需缺失项为0；执行完成与任务成功分开。不挑结果，不改gold迎合候选，报告样本数和复核状态，5题不能证明总体法律准确性。

**English**

Future run records must capture task/experiment IDs, setting, repetition, public-input hash, code revision, model and prompt versions, retrieval configuration and corpus/index hashes, output, disclosures, errors, usage, latency, and trace references. Use fresh case IDs for independent repetitions.

Use task-specific 0–2 items from controlled gold records. Average applicable items within each quality dimension per task, then average across tasks. Required success items must each score 2. Behavior items apply only to the listed settings and are reported separately. Reviewers retain item-level reasons; do not use exact reference wording as the scoring rule.

Agent usage comes from RunInfo.usage. Do not add step totals to it. If tokens_complete is false, token totals are incomplete; unknown costs are null. For baselines, record every actual attempt. Separate active runtime, retrieval/embedding costs, model cost, and reviewer/user waiting time where available. Record whether index construction is excluded from per-task online latency/cost.

Retain failures and partial outputs; missing required output scores zero. Report execution completion separately from task success. Do not cherry-pick runs or change gold to match retrieved candidates. Show sample counts and draft/review status; five tasks cannot establish general legal accuracy.

## Acceptance gates

**中文**

1. 数据门槛：三层数据通过校验，公开任务拒绝私有字段，重复ID、同案跨集、版本不符应失败。
2. 执行门槛：模拟运行通过输入隔离、暂停恢复、失败处理和保存检查。
3. 真实调用门槛：记录模型及价格/配置决定，明确启用付费执行，共享检索及基线可用。
4. 复核门槛：核对输出与引用，报告逐项分数、费用、延迟和失败，标注草稿及未解决依赖。

成员4确认模型兼容及Runtime适配；成员2确认检索快照和来源；成员3复核事实及成功条件。本轮不开展最终测试扩展或SFT。

**English**

1. Current gate: all public/hidden/gold records validate; private fields are rejected in public tasks; duplicate IDs, cross-split case families, and version mismatches fail validation.
2. Execution gate: mock runner passes input-isolation, wait/resume, failure, and result-persistence checks.
3. Paid pilot gate: model and price/configuration choices are recorded, paid execution is explicitly enabled, shared retrieval works, and the baseline runner exists.
4. Review gate: review outputs and citations, report item-level scores, costs, latency, and failures. Mark draft legal annotations and unresolved dependencies explicitly.

Next dependencies: Member 4 confirms real model compatibility and the runtime adapter; Member 2 confirms the retrieval snapshot and source references; Member 3 reviews facts and success conditions. Final-test scale and SFT are outside this first pilot.

## First execution evidence

**中文**

首批结果见[pilot_results_v1.md](pilot_results_v1.md)；实际请求、回复、候选原文、用量和清单在本地`evaluation/results/20261109T104304Z-dd61ec/`。索引使用现有1690条、BAAI/bge-m3和metadata，不代表完整类案库或历史版本覆盖。

基线限制单次生成和至多一次检索。Token/时间超限在供应商调用完成后标记，不是调用中的硬取消。嵌入客户端可最多尝试4次HTTP请求，与工具重试配置独立；无法取得每请求计费用量，嵌入费用保持未知。模型不自动重试，也不重跑挑选输出。

**English**

The first batch is documented in [pilot_results_v1.md](pilot_results_v1.md). Raw
requests, replies, candidate texts, usage, and manifests are local-only under
evaluation/results/20261109T104304Z-dd61ec/. Index construction used all 1,690
current provisions, BAAI/bge-m3, and metadata inputs; no complete case database
or historical-version coverage is implied.

The baseline procedures enforce a single generation and at most one retrieval.
Token/time overruns are marked after provider completion; this is not a hard
in-flight token cancellation mechanism. The existing embedding client may retry
up to four HTTP attempts, independently of the runtime tool retry setting. Its
per-request billed token usage is unavailable here, so embedding cost stays unknown.
There is no automatic model retry or output-selection rerun.
