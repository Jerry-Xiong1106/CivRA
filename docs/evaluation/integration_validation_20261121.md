# 集成回归与四模式咨询试点 / Integration Regression and Four-Setting Consultation Pilot

日期 / Date: 2026-11-21 · 集成版本 / Integration revision: `cf74315` · 批次 / Batch: `20261121T085600Z-80f287`

## 回归覆盖 / Regression coverage

| 流程 / Flow | 本轮验证 / Verified coverage | 验收方式 / Method |
| --- | --- | --- |
| 新建案件、咨询、法条检索 / Case creation, consultation, retrieval | 同源 API 创建案件，真实模型回答并返回法条来源，重启后可读取 / Case creation, live answer and sources, recovery after restart | 本机真实服务冒烟 / Live local smoke test |
| 三条路线与分步操作 / Three routes and step decisions | 协商、调解、诉讼入口与步骤顺序，修改停在原步骤，切回保留进度 / Route order, revision stays on step, route progress survives switching | HTTP 回归，脚本模型 / HTTP regression with scripted model |
| 上传、证据和文书 / Upload, evidence and drafts | 上传与读取范围、证据规则、版本与 Word/PDF 下载 / Upload scope, evidence rules, versions and Word/PDF downloads | API 与业务测试 / API and business tests |
| 恢复与隔离 / Recovery and isolation | 重启继续等待、SSE 游标、重复提交、版本冲突、跨案件隔离 / Resume after restart, SSE cursor, idempotency, conflicts and case isolation | HTTP 与前端自动化测试 / HTTP and frontend tests |
| 四模式咨询 / Four-setting consultation | 5 道开发题，四种模式各运行一次 / Five development tasks, one run per setting | 真实模型与同一向量检索服务 / Live model and shared vector retrieval |

脚本模型回归验证接口与状态变化。扫描件 OCR、真实模型下从路线选择走到完整诉讼文书，以及官方模板填写，需要用独立合成材料联合验收。初次咨询冒烟和本批四模式试点均使用合成或已见开发材料。

Scripted regressions verify contracts and state transitions. Scanned-document OCR, a complete live litigation run, and official form filling need separate acceptance with synthetic materials. The live smoke test and comparison use synthetic or previously seen development inputs.

## 试点结果 / Pilot results

生成使用 `Qwen/Qwen2.5-7B-Instruct`，共用当前法条快照和 `exact-vector` 检索服务。RAG 与 Fixed Workflow 发出一次固定检索，Agent 可以决定是否及如何检索。Fixed Workflow 使用成员 3 的证据检查及回答核验接口，对交互借贷题执行预设的还款追问。该模式在此批次评估咨询流程，公开材料作为文本输入，未做文档解析或结构化证据录入。

All settings use `Qwen/Qwen2.5-7B-Instruct` and share the current statute snapshot. RAG and Fixed Workflow make one predetermined exact-vector search. Agent may decide whether and how to retrieve. Fixed Workflow calls Member 3's evidence and answer-review methods and asks a predefined repayment question for the interactive lending task. The consultation comparison supplies public materials as text without document parsing or structured evidence ingestion.

| 指标 / Metric | Direct LLM | RAG | Fixed Workflow | Agent |
| --- | ---: | ---: | ---: | ---: |
| 技术状态完成 / Runtime completed | 5/5 | 5/5 | 5/5 | 5/5 |
| 交付非空正文 / Delivered nonempty answer | 5/5 | 5/5 | 5/5 | 4/5 |
| 原定必需项暂定通过 / Provisional required-item passes | 3/5 | 4/5 | 4/5 | 2/5 |
| 模型调用 / Model calls | 5 | 5 | 5 | 7 |
| 模型 Token / Model tokens | 2,341 | 12,642 | 13,139 | 55,500 |
| 平均在线耗时 / Mean online duration, s | 5.554 | 9.328 | 10.729 | 18.183 |

评分按已有检查项逐条复核，待团队评审。交互设计题的必需项允许有条件的回答，所以四种模式都通过该项。行为项单列，只有 Fixed Workflow 实际取得补充信息并算出余额，Agent 的交互行为项为 0。各模式只有 5 道已见开发题，费用未知。

Scores are provisional and await team review. All settings pass the required uncertainty item on the interactive-design task because the rubric accepts conditional answers. The behavior item is separate. Fixed Workflow acquired the permitted follow-up and calculated the balance. Agent scored zero on that behavior item. Each setting has five seen development tasks. Costs are unknown.

## 缺陷与交接 / Defects and ownership

| 证据 / Evidence | 观察与处理 / Observation and action | 负责人 / Owner |
| --- | --- | --- |
| `pilot-001`, `run_20261121_83fdb3` | 提供商 `finish_reason=length`，用尽 2,000 个输出 Token，`completed` 的回答为空。评测层已将今后同类结果标记为 `empty_output` 并排除在交付数外，同时保留运行状态和停止原因。Runtime 需核对截断与终态。 / Provider reached the output limit and Runtime returned a completed run with an empty answer. Evaluation now counts it as empty and retains the raw state and stop reason. Runtime should handle truncation and final state. | 成员 5 已修统计，成员 4 核对 Runtime / Member 5 fixed counting, Member 4 owns Runtime |
| `pilot-002`, `run_20261121_70fabc` | Agent 在正文问还款，轨迹却没有 `ask_user` 或待回复状态，控制器无法提供事实。预期对有效请求应发起结构化等待。 / Agent asked about repayment in prose and ended without a resumable wait. A structured wait is expected for follow-up. | 成员 4 / Member 4 |
| `pilot-005`, `run_20261121_7062f2` | Agent 直接结束，未检索，使用了非本题预期的依据。RAG 找到预期条文，正文仍出现前后矛盾的年限。 / Agent finished without retrieval and cited a different authority. RAG retrieved the expected provision but generated contradictory years. | 成员 4 查工具选择，成员 5 查基线输出 / Member 4 reviews tool selection, Member 5 reviews baseline output |
| 本批 RAG 与 Fixed Workflow / RAG and Fixed Workflow | RAG 仅 1/5 份回答有可绑定的显式来源标记，Fixed Workflow 为 0/5。评测已记录实际绑定和未知 ID。核验未判定法律主张是否获原文支持。 / Explicit bindings appear in 1/5 RAG answers and 0/5 Fixed Workflow answers. Evaluation records bindings and unknown IDs. Claim support needs review. | 成员 5 改进基线；成员 2、3 复核来源与判分 / Member 5 improves baselines, Members 2/3 review sources and scores |

## 复现与结果位置 / Reproduction and results

云端评审从[共享结果目录](../../evaluation/results/20261121T085600Z-80f287/README.md)开始。每项问题的离线证据位置、重跑方式、预期和验收条件见[缺陷复现清单](integration_defects_20261121.md)。

Start with the [shared results](../../evaluation/results/20261121T085600Z-80f287/README.md) for review. The [defect checklist](integration_defects_20261121.md) gives the saved evidence, rerun steps, expected behavior, and acceptance criteria for each issue.

`evaluation/results/20261121T085600Z-80f287/` 是这轮本地记录目录。`records.json` 保留全部原始输出和 Runtime 状态，`delivery_summary.json` 记录非空交付和用量，`review_scores.json` 记录逐项理由，`score_summary.json` 汇总评分，`model_stop_evidence.json` 保存已捕获的停止原因。评测修复是在首次运行中发现问题之后完成的，原始结果保持原状。

The local batch directory is `evaluation/results/20261121T085600Z-80f287/`. It contains original runs, delivery counts and usage, item reviews, aggregated scores, and available provider stop reasons. Evaluation fixes landed after the first run exposed the problem. The original records retain their observed states.

```bash
.venv-evaluation/bin/python scripts/evaluation/run_evaluation_pilot.py \
  --four-mode --config evaluation/configs/consultation_pilot_v2.json \
  --hidden evaluation/private/pilot_v1/hidden.jsonl --prompt-key
```

运行在独立临时案件库中。隐藏事实文件需要单独提供，评分程序另行读取 gold 数据。运行清单记录实际用过的代码、数据和索引哈希。

The run uses isolated temporary case storage. Supply the hidden-fact file separately. The scoring command reads gold data separately. The run manifest identifies the implementation, data, and index used.

## v0.5 修复与复测 / v0.5 fixes and rerun

批次 / Batch `20261121T091400Z-80d7da` · 同一组 5 道开发题、模型、检索配置和预算 / Same five development tasks, model, retrieval configuration, and budgets

- RAG 和 Fixed Workflow 的模型输入使用简短候选标记与条文正文。仅将模型真正输出且能在本轮检索中匹配的标记解析为来源 ID，单独记录未知标记。`model_response.content` 保留原文，`output` 保留解析后的答案。标记绑定只核对来源链接，法律支持关系由评审判断。
- RAG and Fixed Workflow send compact candidate labels and source text to the model. Explicit labels are resolved against the current retrieval and unknown labels are recorded. `model_response.content` retains the original response, and `output` contains the answer with resolved labels. Reviewers assess whether the source supports the claim.
- 评测单列 Runtime 完成、完整正文交付、累计 Token 预算和逐项评分。因输出上限中止的部分正文不计入完整交付。新批次在每条记录保存 Token 预算核对，缺少完整用量时标记为未知。
- Evaluation tracks Runtime completion, answer delivery, cumulative token compliance, and item scores. Answers cut short by the output limit are excluded from delivery. New records include a token-budget audit and mark missing usage as unknown.

| 指标 / Metric | Direct LLM | RAG | Fixed Workflow | Agent |
| --- | ---: | ---: | ---: | ---: |
| 交付正文 / Delivered answer, v0.4 → v0.5 | 5 → 5 | 5 → 5 | 5 → 5 | 4 → 5 |
| 有显式来源绑定 / Answers with explicit bindings, v0.4 → v0.5 | — | 1 → 3 | 0 → 4 | — |
| 24,000 Token 内交付 / Delivered within 24,000 tokens, v0.5 | 5/5 | 5/5 | 5/5 | 4/5 |
| 必需评分项暂定通过 / Provisional required-item passes, v0.5 | 3/5 | 5/5 | 5/5 | 3/5 |
| 模型 Token 合计 / Total model tokens, v0.5 | 2,550 | 5,828 | 6,224 | 73,001 |

必需项评分按原定检查项执行，和预算行独立统计。每个模式各执行一次每题。五道已见题的变化仅作为复测观察；团队独立复核和正式对照实验待完成。法条绑定次数和法律准确率需分别看待。

Required-item scores use the existing checklist and are reported independently of budget compliance. Each setting ran once per task. These five seen development tasks record observed rerun behavior. Independent review and the formal comparison are pending. Source binding and legal accuracy require separate checks.

| 题目与负责人 / Task and owner | 观察与验收 / Observation and acceptance |
| --- | --- |
| `pilot-001` · 成员 4 / Member 4 | 本次 Agent 交付正文，原运行的空回答缺陷保留复现。用脚本模型模拟 `finish_reason=length`，验收 Runtime 的终态和部分回答标记。RAG 虽绑定第 676 条，仍直接从未约定借期利息推出不适用逾期利息规则，成员 2、3 核对主张与原文。 / Agent delivered an answer on rerun. Retain the original empty-answer reproduction and verify final state and partial-answer metadata with a scripted length stop. The RAG answer binds article 676 yet draws an unsupported default-interest inference. Members 2/3 should review the claim against the provision. |
| `pilot-002` · 成员 4 / Member 4 | Agent `run_20261121_7bdd92` 返回 `material_request` 等待，请求上传还款记录。现有规则只在问及有无还款或金额时披露文字事实，所以控制器未披露。检查 `pilot-002-agent.json` 的 `disclosures` 和 `disclosure_decisions`。验收时应能发出符合规则的事实问题，并在同一运行中正确使用回复。 / Agent waited for a repayment document. The existing controller discloses a text fact only when asked whether repayment occurred or how much was repaid. Inspect the wait and disclosure decision, then verify that an eligible question receives a reply and the same run applies it. |
| `pilot-002` · 成员 4 / Member 4 | 同一次运行累计 24,852 Token，Runtime 状态为 `completed`。配置传入 24,000，回复后 Runtime 开启新一轮预算。成员 5 已核对整题用量。成员 4 需确认评测整题预算怎样跨回复执行和返回终态。 / The same completed run consumed 24,852 tokens against a 24,000-token evaluation limit. Runtime starts a fresh budget after a reply. Evaluation now checks cumulative usage. Member 4 owns enforcement and terminal status across replies. |
| `pilot-005` · 成员 4、2、3 / Members 4, 2, and 3 | RAG 与 Fixed Workflow 均绑定第 705 条，前后使用一致的二十年边界。Agent `run_20261121_ae5dd2` 未检索，正文同时将超出部分写为有效和无效。检查模型停止原因、工具轨迹和最终正文。验收核对来源及同一答案的规则一致性。 / Both retrieval baselines bind article 705 and apply a consistent twenty-year threshold. Agent skipped retrieval and used inconsistent wording for the excess term. Compare provider stop reasons, tool trace, and final answer, then verify source support and internal consistency. |

本地批次 `evaluation/results/20261121T091400Z-80d7da/` 包含 `records.json`、`delivery_summary.json`、`review_scores.json`、`score_summary.json` 和 `token_budget_audit.json`。运行清单记录工作目录处于修改中及实际评测实现的哈希。累计预算文件从已保存的原始记录生成。

The local batch includes raw records, delivery and usage metrics, item-level reasons, score summary, and the cumulative budget audit. The manifest records the dirty working tree and the evaluation implementation hashes. The audit was derived from the saved runs.

```bash
.venv-evaluation/bin/python scripts/evaluation/run_evaluation_pilot.py \
  --four-mode --config evaluation/configs/consultation_pilot_v3.json \
  --hidden evaluation/private/pilot_v1/hidden.jsonl --prompt-key
```

### 人工复核与扩展开发题 / Human review and expanded development set

原 5 题路径保留。扩展开发集为 [`tasks_extended.jsonl`](../../evaluation/datasets/pilot/tasks_extended.jsonl)，共 9 道静态题和 1 道交互题。新增题覆盖提前还款约定、同意转租、交付差额、费用证据和材料日期冲突。扩展集通过公开题、隐藏事实及评分草案一致性校验。下节记录扩展后的在线运行。扩展运行使用 `consultation_pilot_extended.json`、扩展公开题及本地 `evaluation/private/pilot_v2/hidden.jsonl`。

The existing five-task path is retained. The expanded development set has nine static and one interactive task. The new tasks cover early repayment terms, consent to sublet, delivery shortfall, evidence for expenses, and conflicting document dates. Public inputs, hidden facts, and draft annotations passed bundle validation. The expanded live run is documented below. It uses `consultation_pilot_extended.json`, the expanded public set, and local hidden records in `evaluation/private/pilot_v2/hidden.jsonl`.

```bash
BATCH=evaluation/results/20261121T091400Z-80d7da
GOLD=evaluation/private/pilot_v1/gold.jsonl
.venv-evaluation/bin/python scripts/evaluation/prepare_evaluation_review.py \
  --batch-dir "$BATCH" --gold "$GOLD"
```

已为本批生成 16 份静态回答的 `static_review_packet.json`。向评审提供该文件，保留 `static_review_key.json` 给汇总人员。静态评分记录填写 `review_id`、`item_id`、0–2 整数 `score`、`reason`、`reviewer` 和 `review_kind`。交互与行为项另看原始记录，填写 `experiment_run_id`。汇总前将全部评分项放入一个 JSON 数组。使用 `review_evaluation_pilot.py --review-key "$BATCH/static_review_key.json"` 解码静态评分，并用 `--output` 保存独立复核结果。回答正文可能提供模式线索。

The generated `static_review_packet.json` contains 16 static answers with setting labels masked. Share the packet with reviewers and keep `static_review_key.json` with the coordinator. Each static rating needs `review_id`, `item_id`, integer `score` from 0 to 2, `reason`, `reviewer`, and `review_kind`. Use the original records and `experiment_run_id` for interactive and behavior items. Combine all ratings in a JSON array before aggregation, pass the key via `--review-key`, and save the independent summary with `--output`. The answer text may provide clues about its setting.

### 扩展试点结果 / Ten-task development pilot

批次 / Batch `20261121T092259Z-0ffa9f` · 10 题 × 4 模式 / 10 tasks × 4 settings

| 指标 / Metric | Direct LLM | RAG | Fixed Workflow | Agent |
| --- | ---: | ---: | ---: | ---: |
| 在 24,000 Token 上限内交付 / Delivered within the 24,000-token limit | 10/10 | 10/10 | 10/10 | 10/10 |
| 必需项暂定通过 / Provisional required-item passes | 4/10 | 7/10 | 9/10 | 6/10 |
| 含显式来源绑定 / Answers with explicit bindings | — | 8/10 | 7/10 | — |
| 模型 Token 合计 / Total model tokens | 5,972 | 13,033 | 13,646 | 92,627 |
| 平均在线耗时 / Mean online duration, s | 9.551 | 7.232 | 9.430 | 7.477 |

每个模式执行 9 道静态题和 1 道交互题，每题一次。全部记录包含用量和耗时。在线耗时含模型及调用过程，索引构建在运行前完成。价格版本和嵌入计费用量未取得，费用未知。评分来自本地暂定逐项复核。36 份静态回答已生成隐藏模式标签的复核包。

Each setting ran nine static tasks and one interactive task, once per task. All records include measured usage and duration. Online timing includes model and related calls. Index construction took place before the run. Costs are unknown because a versioned price table and billed embedding usage are unavailable. Item scores are provisional. A review packet with setting labels masked is ready for all 36 static answers.

独立评审各自保存评分。组内核对分歧后，为每个评分项提交一条共识记录给汇总程序，评审原始文件另存。

Reviewers retain their independent ratings. After adjudication, submit one consensus rating per item to the aggregator and retain the original review files separately.

| 题目 / Task | 本轮观察与复现位置 / Observation and reproduction | 交接 / Owner |
| --- | --- | --- |
| `pilot-002` | Agent `run_20261121_4eba1b` 在正文询问还款情况后直接完成，`disclosures` 为空。Fixed Workflow 获取了文字事实并算出 8,000。观察两种模式的等待记录和最终回答。 / Agent asked in prose and ended with no disclosure. Fixed Workflow obtained the text fact and calculated 8,000. Compare the wait records and answers. | 成员 4 核对结构化追问 / Member 4 verifies structured follow-up |
| `pilot-006` | RAG 命中预期的第 677 条，最终仍把实际借款期间写成一年。比较 `pilot-006-rag.json` 的 `retrieval.matches` 和 `output`。 / RAG retrieved article 677 but assumed the actual period was one year. Compare the candidate text with the final answer. | 成员 5 保留基线用例，成员 3 复核事实支持 / Member 5 retains the baseline case, Member 3 reviews fact support |
| `pilot-008` | 两个固定检索模式未把标注预期的第 577 条放入前五，回答计算出差额为四把，但引用了承揽通知或运输赔偿依据。查看 `pilot-008-rag.json` 和 `pilot-008-fixed_workflow.json` 的候选与正文。 / Both fixed-search settings missed the expected article 577 in their top five and paired the correct four-chair gap with rules about undertaking or carriage. Inspect the candidate lists and answers. | 成员 2 核对召回，成员 3 核对规则适用 / Member 2 checks recall, Member 3 reviews applicability |
| `pilot-010` | 四种模式均保留两个有冲突的日期。Direct LLM 未列举独立佐证，其余三种给出可追查材料。 / All settings retained the two conflicting dates. Direct LLM did not name independent corroboration; the other three did. | 成员 5 保留证据冲突评分项 / Member 5 retains the evidence-conflict item |

复现从仓库根目录运行，密钥通过交互提示输入。运行完后，用同一批次的路径生成汇总和图，`--chart` 需要安装 Matplotlib。结果中的原始记录、评分理由、汇总及图表均保留在本地。

Run from the repository root and enter the key at the prompt. Pass the new batch directory to generate the comparison and chart. The chart option requires Matplotlib. Raw runs, reviews, summaries, and graphs are kept locally.

```bash
.venv-evaluation/bin/python scripts/evaluation/run_evaluation_pilot.py \
  --four-mode --config evaluation/configs/consultation_pilot_extended.json \
  --public evaluation/datasets/pilot/tasks_extended.jsonl \
  --hidden evaluation/private/pilot_v2/hidden.jsonl --prompt-key

.venv-evaluation/bin/python scripts/evaluation/compare_evaluation_pilot.py \
  --batch-dir evaluation/results/20261121T092259Z-0ffa9f --chart
```

本轮本地回归通过 Python 592 项测试及 205 项子测试、前端 54 项测试。完成正式验收仍需评审独立评分、冻结正式集、确认一致的 Runtime 预算及路线/文书能力，并执行正式四模式实验。

Local regression passed 592 Python tests, 205 subtests, and 54 frontend tests. Formal acceptance still requires independent scoring, a frozen test set, confirmed Runtime budgets and route/document capabilities, and the final four-setting experiment.
