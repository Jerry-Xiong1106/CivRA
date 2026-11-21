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
