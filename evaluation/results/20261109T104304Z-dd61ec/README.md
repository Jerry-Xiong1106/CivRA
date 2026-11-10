# 首次开发试点：待团队复核 / First Development Pilot: Team Review Pending

批次 / Batch: `20261109T104304Z-dd61ec`
运行日期 / Run date: 2026-11-09

**中文**

本批仅用于开发联调和评测规则讨论。5个开发任务各运行Direct LLM/RAG一次，10次执行全部完成。生成模型为`Qwen/Qwen2.5-7B-Instruct`；RAG使用现有1690条法条、BAAI/bge-m3、metadata索引、exact-vector和top_k=5。

| 文件 | 组员可以查看的内容 |
| --- | --- |
| [任务原文](../../datasets/pilot/tasks.jsonl) | 5个公开任务、材料和输出要求 |
| [records.json](records.json) | 全部10次请求、完整回答、候选原文、Token和耗时 |
| `pilot-001-direct_llm.json`等10个单次文件 | 按题目/模式查看，内容与records.json对应项一致 |
| [scoring_checklist.json](scoring_checklist.json) | 每题评分项、0/1/2分标准、适用模式、成功条件与依据引用 |
| [review_scores.json](review_scores.json) | 助手逐项评分、理由、评审身份及待人工复核状态 |
| [score_summary.json](score_summary.json) | 按模式和任务类型汇总，N/A不当作0 |
| [summary.json](summary.json) | 完成/失败/截断/预算耗尽数量 |
| [manifest.json](manifest.json) | 原运行配置、代码版本和数据/索引哈希 |
| [dependencies.txt](dependencies.txt) | 原运行环境依赖 |
| [publication_manifest.json](publication_manifest.json) | 发布范围、限制和文件校验哈希 |

重点字段：`output`是完整回答；`messages`是实际模型输入；`retrieval.matches`是候选法条与来源；`model_response.usage`和`duration_ms`是模型用量与在线时间。Direct的retrieval为null。

评分按[评分规则](../../../docs/evaluation/rubric.md)和[试点协议](../../../docs/evaluation/protocol.md)：适用项为0–2分，必需成功项全部为2分才通过；先按任务内维度平均，再跨任务汇总。当前必需项暂定通过Direct 3/5、RAG 4/5，不等于整段回答都正确。费用未配置价格版本，仍为未知；索引构建不计入在线耗时。

所有标注仍为draft，评分为assistant_provisional。组员复核时请引用`task_id`、`mode`和`item_id`，保留原始评分，在单独评审文件中记录新分数、理由、评审者及分歧处理。不要覆盖首次输出或修改原始gold迎合答案。评分清单是为本次开发复核导出的限定字段，不包含隐藏事实池或完整gold。

需要关注：5份RAG回答都没有输出要求的完整`[source_id]`；Direct租期题有年限矛盾和引用不匹配；利息题两种模式都存在条件表达问题。详见[结果报告](../../../docs/evaluation/pilot_results_v1.md)和[逐题中文说明](../../../docs/evaluation/progress_summary.md)。检索命中不等于引用支持结论。

`manifest.json`保留实际运行时的`git_revision`和`working_tree_dirty=true`，不能替换成发布提交；部分代码之后有改动，报告中已说明。本次发布不重新调用API，保留既有16份结果文件原文，另加说明、规则导出和发布清单。

**English**

This batch supports development integration and evaluation-rule discussion only. Five tasks ran once through each of Direct LLM and RAG, with all ten executions completed. Generation used `Qwen/Qwen2.5-7B-Instruct`; RAG used the current 1,690 provisions, BAAI/bge-m3 metadata vectors, exact-vector, and top_k=5.

`records.json` retains all actual requests, answers, candidates, usage, and timing. The ten per-run files duplicate its corresponding entries. `scoring_checklist.json` publishes item descriptions, 0/1/2 criteria, applicable settings, required-success flags, and authority references. `review_scores.json` preserves assistant scores and reasons; `score_summary.json` aggregates by setting/type. `summary.json` counts execution outcomes. The original manifest and dependency list remain unchanged; the publication manifest adds scope and integrity hashes.

Inspect `output`, `messages`, `retrieval.matches`, `model_response.usage`, and `duration_ms`. Direct LLM has no retrieval. The linked rubric/protocol define applicable 0–2 scoring, all-required-items-at-2 success, and per-task dimension averaging before cross-task aggregation. Provisional success counts are 3/5 and 4/5, not whole-answer correctness. Costs are unknown; online latency excludes index construction.

Annotations remain draft and reviews assistant_provisional. Cite task_id, mode, and item_id in team review; retain the original scores and record new scores, reasons, reviewer identity, and adjudication separately. Do not overwrite first responses or tune gold to the answers. The exported checklist contains only review-relevant fields, not hidden facts or complete gold records.

Known issues: all five RAG outputs omit full source-ID markers, Direct LLM's lease answer has a year-boundary contradiction and mismatched citation, and both interest answers have conditional-scope issues. Consult the linked report and task-by-task progress note. Retrieval does not establish citation support.

The original git revision and dirty-state flag describe execution, not publication. Some code changed afterward as documented. This publication makes no new API requests and preserves the original sixteen evidence files, adding only explanations, the checklist export, and publication metadata.
