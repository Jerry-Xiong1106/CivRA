# 集成缺陷复现清单 / Integration Defect Reproduction

集成版本 / Integration revision `cf74315` · 模型 / Model `Qwen/Qwen2.5-7B-Instruct` · 批次 / Batch `20261121T085600Z-80f287`

[共享运行与评分 / Shared runs and scores](../../evaluation/results/20261121T085600Z-80f287/README.md) · [试点问题 / Pilot inputs](../../evaluation/datasets/pilot/tasks.jsonl)

## 离线核对与现场复跑 / Offline inspection and live rerun

离线打开 `shared_runs.json`，按 `task_id` 和 `mode` 定位原运行，结合 `shared_reviews.json` 查看评分。在线复跑使用下面的命令。需要当前语料的 metadata 向量索引、硅基流动密钥和受控隐藏事实文件。新批次独立保存。即使温度为 0，模型重跑的答案也可能变化，已保存记录是本次复现证据。

Open `shared_runs.json` and filter by `task_id` and `mode`. The live command requires the current metadata vector index, a SiliconFlow key, and the controlled hidden-fact file. It writes a new batch. Model output may vary on rerun at temperature 0. The saved records are the evidence for the observations below.

```bash
.venv-evaluation/bin/python scripts/evaluation/run_evaluation_pilot.py \
  --four-mode --config evaluation/configs/consultation_pilot_v2.json \
  --hidden evaluation/private/pilot_v1/hidden.jsonl --prompt-key
```

| 优先级及归属 / Priority and owner | 复现 / Reproduce | 实际 / Observed | 预期及修复验收 / Expected and acceptance |
| --- | --- | --- | --- |
| P0 · 成员 4 Runtime，成员 5 统计已修 / Member 4 Runtime, Member 5 counting fixed | 在 `pilot-001` 的 `agent` 记录查看 `run_20261121_83fdb3` 的 `output`、`status`、`model_stops`。用 2,000 输出 Token 上限复跑。 / Inspect the Agent record for pilot-001 and rerun with a 2,000 output-token cap. | 首次模型停止原因为 `length`，结果为 `completed` 且正文为空。评测标为 `empty_output`。 / Provider stopped at length. Runtime reported completion with an empty answer. Evaluation flags `empty_output`. | 交付正文或返回可识别的截断/失败状态。加入空答回归用例，确认 UI 与报告不把它计为交付。 / Deliver an answer or expose truncation/failure. Add coverage for empty responses in Runtime and confirm the UI and evaluation exclude them from delivered answers. |
| P1 · 成员 4 / Member 4 | 在 `pilot-002` 的 `agent` 记录查看 `run_20261121_70fabc` 的正文、轨迹与 `disclosure_count`。可用新建借款案件仅提供本金，询问如何核对余额。 / Inspect the repayment question, tool trace, and disclosure count. Use a new loan case with principal only and request a balance check. | 正文提出补充还款情况，轨迹只有 `update_case` 与结束，未进入等待。 / The prose requests repayment information. The trace records `update_case` and finish with no wait. | 需要追问时应返回结构化 `waiting_user`，按 `wait_id` 回复后继续同一运行，正确使用实际披露信息。 / Return a structured wait for an actionable follow-up, then resume the same run using `wait_id` and apply the disclosed fact. |
| P1 · 成员 4 查检索决策，成员 5 查基线一致性 / Member 4 retrieval decisions, Member 5 baseline consistency | 对照 `pilot-005` 的 `agent` 与 `rag`，查看 Agent `run_20261121_7062f2` 的轨迹及 RAG 候选 ID 与正文。 / Compare Agent trace with RAG candidate IDs and answer for the thirty-year lease. | Agent 未检索并使用了与预期语料不同的条款。RAG 找到预期条文，却将超限部分写为从第八年开始。 / Agent cited a different provision without retrieval. RAG retrieved the expected provision but placed the excess at year eight. | 依据可追溯至适用候选，回答各处的年限边界一致。对复测回答核对条款和前后表述。 / Ground the answer in a traceable source and check every stated year boundary for consistency. |
| P1 · 成员 5 输出格式，成员 2、3 复核原文和评分 / Member 5 output format, Members 2/3 source and score review | 筛选 RAG 和 Fixed Workflow，检查 `explicit_citation_count`、`retrieved_sources` 及 `pilot-004` 的显式标记。 / Check explicit citation counts and retrieved IDs, using pilot-004 as a marker example. | RAG 1/5、Fixed Workflow 0/5 含可绑定的显式来源 ID。 / One RAG answer and zero Fixed Workflow answers contain bindable IDs. | 法律主张需要依据时，输出真实候选 ID 及主张位置。缺少依据的题保留说明。团队人工核对原文支持关系。 / Bind supported claims to actual candidate IDs, explain insufficient evidence when relevant, and review claim support separately. |

成员 5 已完成这轮集成回归、咨询试点和评测侧统计修复。正式评测仍需扩展及冻结独立测试集、完成引用格式改进与复测、组织交叉复核，并执行正式实验。扫描件 OCR 与真实模型的完整路线/文书联调需要对应模块负责人共同提供验收条件。

Member 5 completed this integration regression, consultation pilot, and delivery-count correction. Formal evaluation still requires a frozen independent test set, citation-format improvements and reruns, cross-review, and the final experiment. Scanned OCR and full live route/document acceptance require the relevant module owners.
