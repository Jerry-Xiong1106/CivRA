# 评分规则 / Scoring rubric

**中文**

状态：草案v0.1。负责人：成员5。

**English**

Status: Draft v0.1. Owner: Member 5.

## Scoring rules

**中文**

按任务专用检查表评价实际输出，接受有依据的不同表述。最终测试前冻结检查表与汇总规则。

| 分数 | 含义 |
| --- | --- |
| 0 | 错误、缺失，或与证据/依据存在重大矛盾 |
| 1 | 部分满足，有重要遗漏或解释不完整 |
| 2 | 正确且充分满足该项 |
| N/A | 依据事先规定，本任务不要求该项 |

系统失败导致必需内容缺失应记0分，不能记N/A。质量、行为和资源指标分别报告，不合成未定义的总分。

**English**

Score actual outputs against task-specific checklists. Accept supported alternatives rather than exact reference wording. Freeze the checklists and aggregation rules before final testing.

| Score | Meaning |
| --- | --- |
| 0 | Incorrect, missing, or materially inconsistent with evidence or authorities |
| 1 | Partially satisfied, with important omissions or incomplete explanation |
| 2 | Correct and sufficient for the item |
| N/A | Not required under the task's predefined applicability rules |

Required content missing because of a system failure scores zero; it is not N/A. Report quality dimensions, behavior, and resource measurements separately rather than combining them into an undefined overall score.

## Quality dimensions

**中文**

| 维度 | 检查内容 |
| --- | --- |
| 法律与任务结果 | 争议识别、依据及版本、事实前提、目标覆盖和不确定性 |
| 事实与证据 | 提取、来源关联、缺口、冲突及适用时的更新处理 |
| 引用可靠性 | 来源真实性、定位、对主张的支持及版本适用性 |
| 流程与文书 | 路线一致、步骤与材料、字段金额及缺失标记 |

链接或条款名称正确不等于支持关系成立。任务要求依据而输出缺少依据时，按缺失项评分。不强求任务未要求的文书或流程。

任务成功按事先定义的必要条件单独判断，高平均分不能抵消必要条件失败。

**English**

| Dimension | Checks |
| --- | --- |
| Legal and task outcomes | Issue identification, applicable authorities and versions, factual premises, user-goal coverage, and uncertainty |
| Facts and evidence | Fact extraction, source linkage, missing information, conflicts, and updates where applicable |
| Citation reliability | Source existence, locator accuracy, support for the associated claim, and version applicability |
| Guidance and documents | Selected-route consistency, required steps and materials, field and amount consistency, and explicit missing fields |

A valid link or provision name alone does not establish citation support. If authority is required but absent, score the corresponding missing item. Do not require documents or procedural outputs that the task did not request.

Determine task success separately using the task's required conditions. A high average score does not override a failed required condition.

## Example items

**中文**

| 检查项 | 0分 | 1分 | 2分 |
| --- | --- | --- | --- |
| 识别部分还款 | 遗漏或否认 | 提到但缺金额/影响 | 识别有材料支持的金额并体现在余额中 |
| 处理金额冲突 | 忽略或无依据采信一方 | 指出冲突但不定位差异 | 定位差异并说明核实需求或有效追问 |
| 识别缺失事实 | 编造或忽略缺口 | 笼统说明信息不足 | 说明具体缺口、影响和针对性请求/条件结论 |

按任务调整示例，它们不是通用法律答案模板。

**English**

| Item | 0 | 1 | 2 |
| --- | --- | --- | --- |
| Identify a documented partial repayment | Omitted or denied | Mentioned without its amount or effect | Supported amount identified and reflected in the balance |
| Handle conflicting amounts | Conflict ignored or resolved without support | Conflict noted without the discrepancy or unresolved facts | Discrepancy located and verification needs or an effective question provided |
| Identify a missing fact | Fact invented or gap ignored | General statement that information is missing | Specific gap identified with its impact and a targeted request or conditional conclusion |

Adapt examples to each task; they are not universal legal-answer templates.

## Behavior review

**中文**

按实际轨迹评价工具选择、参数、顺序、追问与恢复。依据任务定义合理行为，允许多条有效路径。

不具备某能力的基线，对应行为项记N/A，输出质量仍正常评分。日志不足无法判断时记“不可评估”，单独报告轨迹覆盖率；不能由回答流畅推断工具正确。

**English**

Use execution traces to assess tool choice, arguments, ordering, clarification quality, and error recovery. Define expected behavior from the task, not from one preferred trajectory; multiple valid action sequences may exist.

For settings without a capability, mark the corresponding behavior item N/A while scoring output quality normally. If missing logs prevent judgment, mark the item unevaluable and report trace coverage. Do not infer tool correctness from a fluent answer.

## Programmatic measurements

**中文**

统计执行完成率和失败类型、含重试的模型/工具调用、可取得的输入输出Token及覆盖范围、按协议计时的端到端延迟，以及注明币种和价格版本的实际/估算费用。

区分空检索与工具错误。未知用量或费用不是0；平均值同时报告测量覆盖率。缓存、重试和用户等待时间遵循冻结后的口径。

**English**

- Execution completion rate and failure types.
- Model and tool calls, including recorded retries.
- Available input/output token usage and its coverage.
- End-to-end latency under the protocol's timing boundaries.
- Billed or estimated cost, including currency and price-table version.

Distinguish an empty retrieval result from a tool error. Unknown usage or cost is missing data, not zero. Report measurement coverage alongside averages. Follow the frozen rules for caching, retries, and user-wait time.

## Aggregation

**中文**

每个质量维度先对任务内适用项取平均，再对适用任务取平均，同时报告任务数和检查项数；N/A不进分母，避免检查项多的任务获得隐含高权重。

系统失败仍进入执行完成率分母；可见的部分输出正常评分，缺失必需项为0。任务成功与技术完成分开报告。

重复运行先按任务汇总，保留次数和变异，不挑最佳结果。静态/交互任务分开报告，样本足够时再按类型与领域细分。基础设施排除或重跑注明原因、数量及小样本限制。

**English**

For each quality dimension, first average applicable item scores within each task, then average across applicable tasks. Report task and item counts. Exclude N/A items from denominators. This prevents tasks with more checklist items from receiving unintended extra weight.

System failures stay in execution-completion denominators. Score observable partial output normally and missing required items as zero. Report task success separately from technical execution completion.

When using repeated runs, report the declared number of attempts and aggregate repetitions within each task before task-level summaries. Preserve variability rather than selecting the best run.

Report static and interactive results separately, with task-type and domain breakdowns where sample sizes permit. Document infrastructure exclusions and reruns, including their counts and reasons. State the limits of small samples.

## Human review

**中文**

隐藏系统模式标签并随机排列输出，先用开发样例校准评审。条件允许时两人独立评分。

保留原始分数、分歧、裁决结果和理由，报告双人覆盖率及实际使用的一致性指标。模型评分只能辅助，不能替代法律标注核验。

**English**

Hide setting labels and randomize output order. Calibrate reviewers on development examples before final scoring. Use two independent reviewers where feasible.

Preserve original scores, disagreements, adjudicated scores, and reasons. Report double-review coverage and the agreement statistic used. Model-based scoring may assist review but does not replace legal annotation verification.

## Failure analysis

**中文**

按事实提取、追问、检索、版本适用、引用、计算、工具执行、流程控制或文书生成归类。每例记录任务/运行ID、可观察证据、影响与改进建议。

区分已观察的问题和未验证的根因；比较时同时分析成功恢复与未解决失败。

**English**

Classify failures by fact extraction, clarification, retrieval, version applicability, citation, calculation, tool execution, workflow control, or drafting. For each case, record task/run identifiers, observable evidence, impact, and a proposed improvement.

Separate observed failures from unverified root-cause hypotheses. Include both successful recovery and unresolved failure when comparing settings.
