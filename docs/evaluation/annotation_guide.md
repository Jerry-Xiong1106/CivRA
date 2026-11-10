# 数据标注指南 / Annotation guide

**中文**

状态：草案v0.1。负责人：成员5。

**English**

Status: Draft v0.1. Owner: Member 5.

## Task coverage

**中文**

在约定的合同、借贷、租赁、侵权赔偿范围内设计任务。扩展试点时，信息完整、信息缺失、证据冲突、描述模糊和多个争议五类各约两题；可使用多个标签，但应明确主要目标。

核心任务不包含复杂伤残评定、专业医学判断和复杂保险合同争议。

使用去标识化公开案例或明确注明的合成场景。合成题在正式测试前同样需要核验法律标注；模型生成答案只是待复核草稿，不是法律标准答案。

**English**

Prepare tasks within the agreed civil-dispute scope: contracts, lending, leasing, and tort compensation. For the pilot, aim for two tasks in each category: complete information, missing information, conflicting evidence, ambiguous requests, and multiple issues. Multiple tags are allowed; identify a primary objective.

Exclude complex disability assessments, professional medical judgments, and complex insurance-contract disputes from core tasks.

Use de-identified public cases or explicitly labeled synthetic scenarios. Synthetic tasks still require verified legal annotations before formal testing. Model-generated answers are drafts requiring review, not legal ground truth.

## Information layers

**中文**

| 信息层 | 内容 | 访问范围 |
| --- | --- | --- |
| 公开输入 | 初始描述和允许读取的材料 | 被测模式可读取 |
| 隐藏事实 | 补充信息及披露规则 | 仅交互控制程序读取 |
| 标准标注 | 关键事实、依据、可接受结论和评分项 | 仅评审与评分程序读取 |

三层信息分别存储和加载。标准标注不得进入模型提示词、工具回复或检索索引；目录命名不能替代隔离，应检查实际加载路径与输入记录。

**English**

| Layer | Contents | Access |
| --- | --- | --- |
| Public input | Initial description and permitted materials | Available to the evaluated setting |
| Hidden facts | Additional information and disclosure rules | Available only through the interaction controller |
| Gold annotations | Key facts, authorities, acceptable conclusions, and scoring items | Available to reviewers and scoring code only |

Store and load these layers separately. Gold annotations must not enter model prompts, tool responses, or retrieval indexes. Directory names alone do not establish isolation; inspect the actual loading paths and recorded inputs.

## Task contract v0.1

**中文**

已实现的定义见[tasks.py](../../evaluation/schemas/tasks.py)。`PublicTask`、`HiddenTask`和`GoldTask`分别存成JSONL，通过`task_id`和`dataset_version`关联；拒绝未知字段。`to_model_input()`仅输出`initial_input`、`user_goal`、`selected_route`、`materials`和`output_requirements`，不能把完整标注包传给模型。

下表描述概念信息；实际字段和必填项以Python及生成的JSON Schema为准。例如材料实际存于`materials`，复核信息在`GoldTask`中；时间线目前保留在公开材料正文。

公开任务位于`evaluation/datasets/pilot/tasks.jsonl`；隐藏事实和gold位于被Git忽略的`evaluation/private/pilot_v1/`。全新克隆可独立检查公开输入；完整关联校验需要本地受控标注。

试点使用`phase=pilot`、`split=development`。正式最终测试要求标注已复核且有评审者。必须合并检查全部划分清单，才能发现跨开发/测试集的同案泄漏。

| 概念字段 | 含义 |
| --- | --- |
| `task_id` | 唯一任务标识 |
| `source_case_id` | 原始案件/案件族，用于划分隔离 |
| `split` | 数据划分；试点属于开发数据 |
| `task_type`, `domain`, `tags` | 交互类型、领域与覆盖标签 |
| `initial_input` | 初始用户描述 |
| `available_materials` | 材料标识、定位和权限；实际字段为`materials` |
| `timeline` | 相关时间，未知值明确标记；目前在材料正文中 |
| `user_goal`, `selected_route` | 用户目标与确认的路线 |
| `hidden_facts` | 隐藏事实、披露条件和标准回复，单独存储 |
| `gold_annotations` | 复核专用事实、问题、依据、可接受输出和评分项 |
| `provenance` | 来源、合成标记和使用条件 |
| `review_status`, `version` | 复核决定与修订记录 |

**English**

The implemented contracts are in [tasks.py](../../evaluation/schemas/tasks.py).
PublicTask, HiddenTask, and GoldTask are separate JSONL records joined by task_id
and dataset_version. Unknown fields are rejected. PublicTask.to_model_input()
exports only initial_input, user_goal, selected_route, materials, and output_requirements.
Never pass a complete annotation bundle to a model.

The table below describes conceptual information. Actual field names and required
fields are defined by the Python contracts and generated JSON Schema files;
for example, available materials are stored in materials and review metadata is
stored in GoldTask. Timeline facts currently remain in the public material text.

Public pilot examples live in evaluation/datasets/pilot/tasks.jsonl. Local-only
hidden.jsonl and gold.jsonl live in evaluation/private/pilot_v1/. They are draft
annotations and are ignored by Git. A fresh checkout needs these controlled files
only for full bundle validation; public-only validation works independently.

Pilot tasks have phase=pilot and split=development. Formal final-test records
require reviewed annotations and named reviewers. Validate the full split manifest
together to detect case families crossing development and final-test splits.

| Field | Purpose |
| --- | --- |
| `task_id` | Unique task identifier |
| `source_case_id` | Original case or case-family identifier for split isolation |
| `split` | Pilot/development/final-test assignment; pilot is development data |
| `task_type`, `domain`, `tags` | Interaction type, legal domain, and coverage categories |
| `initial_input` | User description supplied at the start |
| `available_materials` | Material identifiers, locators, and access scope |
| `timeline` | Relevant dates, with unknowns explicitly marked |
| `user_goal`, `selected_route` | Requested outcome and any confirmed route choice |
| `hidden_facts` | Facts, disclosure conditions, and standard replies; stored separately |
| `gold_annotations` | Review-only facts, issues, authorities, acceptable outputs, and scoring items |
| `provenance` | Original sources, synthetic status, and usage conditions |
| `review_status`, `version` | Review decisions and revision history |

## Facts and evidence

**中文**

区分用户主张、材料记载、已确认事实和推断。关键事实关联材料ID及位置；保留冲突说法，不无依据地选定一方。

标注缺失信息及其影响的结论或行动，不能用编造事实填空。计算记录输入、单位、假设和预期结果。

**English**

Distinguish user claims, statements in materials, confirmed facts, and inferences. Link important facts to material identifiers and source locations. Preserve conflicting accounts rather than selecting one without support.

Annotate missing information and explain which conclusion or action it affects. Never fill a gap with an invented fact. Record calculations with inputs, units, assumptions, and expected results.

## Legal authorities and acceptable outputs

**中文**

记录依据的来源、条款、版本、生效信息和获取日期，并检查与案件时间及争议的关联。类案注明相似点、差异和比较限制。

列出必答问题、可接受结论的事实前提、必要引用和未解决事项。允许多种有依据的答案，不要求逐字匹配。

尊重用户路线选择，协商或调解并非所有案件必经；仅在任务要求时生成起诉状。未经校准的胜诉概率不能作为标准答案。

每个评分项注明适用性、支持材料、0–2分标准，以及是否属于任务成功的必要条件。

**English**

Record each authority's source, provision, version, effective information, and retrieval date. Check relevance to the incident timeline and issue. For reference cases, record similarities, differences, and limits on comparison.

List required issues, factual premises for acceptable conclusions, necessary citations, and unresolved questions. Accept multiple legally supported answers; do not require exact wording.

Respect the user's route choice. Voluntary negotiation or mediation is not a universal prerequisite. Require a complaint only when the task requests litigation drafting. Do not annotate uncalibrated winning probabilities as gold answers.

For each scoring item, specify applicability, supporting material, 0–2 criteria, and whether it is a required task-success condition.

## Hidden-fact rules

**中文**

每条隐藏事实包含标识、内容、披露条件和标准回复。按语义定义可接受请求，不只依赖关键词。

不披露未被问到的信息；重复提问回复一致；未知或无法提供的内容明确标记。记录请求、回复、命中事实ID和模糊请求的裁决。

试点时用不同表述的问题检查披露规则，最终测试前冻结规则。

**English**

Each hidden fact needs an identifier, content, disclosure condition, and standard response. Define acceptable semantic requests rather than relying only on exact keywords.

Do not reveal facts that were not requested. Repeated questions return consistent information. Mark unknown or unavailable information explicitly. Log each request, response, matched fact identifier, and ambiguous-request adjudication.

Review disclosure rules with paraphrased questions during the pilot. Freeze the policy before final testing.

## Splits and review

**中文**

原始案件及全部改写题放在同一数据划分。开发时看过的任务不再属于未见测试。检查最终测试来源案件和相关裁判是否已从检索数据排除，并保留记录。

成员2支持法律依据与检索标注；成员3支持事实、证据、计算及业务输出标注；成员5维护格式、划分清单和复核历史；全员交叉复核。

法律标注未核验的任务仅用于开发。保留原始分歧及解决记录，不覆盖历史。

**English**

Keep each original case and all derivatives in one split. Tasks inspected during development are not unseen final tests. Check final-test source cases and related judgments against searchable case data and retain the exclusion record.

Member 2 supports authority and retrieval annotations. Member 3 supports facts, evidence, calculations, and business-output annotations. Member 5 maintains the format, split manifest, and review history. All members contribute cross-review.

Tasks with unverified legal annotations remain development-only. Preserve disagreements and their resolution rather than overwriting the original review history.

## Acceptance checklist

**中文**

- 标识唯一，来源完整。
- 用户主张、已确认事实和推断分开。
- 重要事实与冲突有材料定位。
- 缺失事实及披露条件明确。
- 依据已核验，替代结论有支持。
- 评分项可操作，成功条件明确。
- 不含可识别个人信息。
- 同案划分与检索泄漏检查通过。
- 复核状态和版本历史完整。

**English**

- Unique identifiers and complete provenance.
- Clear separation of claims, confirmed facts, and inferences.
- Material locators for important facts and conflicts.
- Explicit missing facts and disclosure conditions.
- Verified authorities and supported alternative conclusions.
- Applicable scoring items and explicit success conditions.
- No identifiable personal information.
- Case-family split isolation and retrieval-leakage checks.
- Recorded review status and version history.
