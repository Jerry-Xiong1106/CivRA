# 渐进式 Agent 质量复测 / Progressive Agent quality rerun

2026-11-21 · 批次 / Batch `20261121T100114Z-921b75`

按渐进式工作流检查回答质量，四种模式共用 DeepSeek Flash 非思考模式和同一组 10 道开发题，重新完成 40 次在线运行。取消整题累计 Token 上限，保留单次 2,000 输出 Token、12 次模型调用、8 次工具调用、300 秒及 3 轮交互限制。Token 用量只记录。旧有上限试验保留为资源约束条件下的历史记录。

All four settings reran the same ten development tasks using DeepSeek Flash with thinking disabled. The cumulative token cap was disabled. The per-call output cap of 2,000 tokens, 12 model calls, eight tool calls, 300 seconds and three interaction turns were retained. Usage was recorded. The earlier capped experiment remains historical evidence of resource-constrained behavior.

| 指标 / Metric | Direct LLM | RAG | Fixed Workflow | Agent |
| --- | ---: | ---: | ---: | ---: |
| 交付非空且未截断的正文 / Delivered nonempty, untruncated answer | 10/10 | 10/10 | 10/10 | 10/10 |
| 必需内容项暂定通过 / Provisional required-content passes | 9/10 | 9/10 | 10/10 | 6/10 |
| 来源支持或条文表述问题 / Source support or wording flagged | 1 | 2 | 0 | 3 |
| 缺主张到来源的链接 / Missing claim-to-source links flagged | 0 | 0 | 0 | 4 |

“交付”核对运行完成和最终正文。“内容通过”沿用运行前的逐题必需项，未将 Token 或耗时计入。引用诊断另外检查条文条件、款次、支持关系和主张链接。图中的引用问题包含两类，缺链接只说明追溯不充分。未标问题的回答仍需人工复核。每题只运行一次，评分由助手暂定，待成员 2、3 交叉复核。

Delivery checks final status and answer text. Content success uses the pre-run required items with no token or latency penalty. Citation diagnostics check source conditions, paragraph numbers, claim support and source links. Missing links indicate incomplete traceability. Unflagged answers still need review. Each task ran once and scores are provisional pending Members 2/3 cross-review.

## 流程问题 / Workflow findings

- 还款题 `pilot-002` 成功追问并计算余额。中途 `update_case` 因覆盖事实缺明确确认被拒绝，错误为 `INVALID_ARGS`，随后恢复。该保护生效，成员 4 需核对模型怎样在更新前取得确认。
- Repayment task `pilot-002` successfully clarified and calculated the balance. One update was rejected because superseding a fact requires explicit confirmation. Recovery succeeded. Member 4 should check confirmation before updates.
- `pilot-006/008` 各有一次 `search_statutes` 返回 `unsupported_exact_query`，工具要求逐条提供完整法律名称与条号。Agent 后续成功检索并完成。成员 4 核对调用格式，成员 2 核对错误提示与工具描述的一致性。
- One search in each of `pilot-006/008` returned `unsupported_exact_query`. The tool requires an explicit law name and article number. Agent recovered with successful searches. Members 4/2 should align generated arguments and documented tool constraints.
- `pilot-006` 最终只说“已列出待核对事项”，没有真正列出。按既有检查项计 1 分。`pilot-008` 给出四把差额，但声称完成了材料清单，正文没有清单，计 1 分。成员 4 核对最终回答是否仅保留了总结。
- The final answer for `pilot-006` says that open questions were listed but does not list them. `pilot-008` calculates four chairs but claims to provide a checklist that is absent. Both receive one on their existing item. Member 4 should check the final-answer construction.

## 回答与引用问题 / Answer and citation findings

- Agent `pilot-001` 对第 680 条的自然人借款条件作过宽概括。`pilot-004` 将“没有约定利息”的规则限定在自然人，并与“约定不明”条件混合。`pilot-005` 正确指出二十年界限，却将超限部分后果写成“第二个十年”。成员 2、3 按本地条文快照逐句复核。
- Agent broadens article 680 in `pilot-001`, confuses absent and unclear interest terms in `pilot-004`, and refers to the second decade when describing the excess-term consequences in `pilot-005`. Members 2/3 should compare the wording against the archived provisions.
- Agent `pilot-002/006/007/009` 的最终主张缺可定位来源链接，其中部分回答有自然语言条号，最终结构化 `citations` 为空。该诊断单独记录。
- Agent answers for `pilot-002/006/007/009` lack claim-to-source links. Some contain article numbers while final structured citations are empty. This is recorded separately.
- Direct LLM `pilot-004` 将第 680 条的款次和内容对应错误。RAG `pilot-002` 把逾期利息等规则过窄地限定在已约定利息；`pilot-005` 把“第三十年”写成整个超出二十年的部分。
- Direct LLM mismatches paragraph numbers in `pilot-004`. RAG narrows default-interest conditions in `pilot-002` and identifies only year thirty when describing the excess period in `pilot-005`.

## 结果和复现 / Results and reproduction

[共享结果与新图](../../evaluation/results/20261121T100114Z-921b75/README.md)包含允许分享的正文、逐题评分和质量问题。交互题正文及披露内容在本地受控文件中。新图显示交付、必需内容和引用诊断，图中每个模式的分母均为 10。

The [shared result package and new chart](../../evaluation/results/20261121T100114Z-921b75/README.md) contain shareable answers, item scores and quality findings. Interactive answers and disclosed facts remain controlled locally. Each setting has a denominator of ten in the delivery, content and citation chart.

```bash
.venv-evaluation/bin/python scripts/evaluation/run_evaluation_pilot.py \
  --four-mode --config evaluation/configs/consultation_quality_deepseek.json \
  --public evaluation/datasets/pilot/tasks_extended.jsonl \
  --hidden evaluation/private/pilot_v2/hidden.jsonl
```
