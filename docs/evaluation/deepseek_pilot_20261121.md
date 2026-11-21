# DeepSeek 四模式开发试点 / DeepSeek four-setting development pilot

2026-11-21 · 批次 / Batch `20261121T094615Z-d64eba`

10 道已见开发题，每种模式各跑一次。四种模式均通过 DeepSeek 官方 API 使用 `deepseek-flash`，关闭思考模式，与本地集成 Agent 的配置一致。任务、24,000 Token 累计上限、单次输出上限、法条索引和控制器沿用前次扩展试点。查询嵌入仍使用硅基流动。DeepSeek Flash 的非思考模式使用服务端固定温度，因此两个供应商的实际采样设置不能视为一致。[官方接口说明](https://api-docs.deepseek.com/api/create-chat-completion/)

The trial ran each of ten seen development tasks once per setting. All settings used `deepseek-flash` through the official DeepSeek API with thinking disabled, matching the local integrated Agent. Task inputs, the 24,000-token cumulative cap, the per-call output cap, the retrieval index and disclosure controller carry over from the preceding pilot. Query embeddings still use SiliconFlow. DeepSeek Flash fixes temperature server-side in non-thinking mode, so effective sampling settings differ across providers. [Official API documentation](https://api-docs.deepseek.com/api/create-chat-completion/)

| 指标 / Metric | Direct LLM | RAG | Fixed Workflow | CivRA Agent |
| --- | ---: | ---: | ---: | ---: |
| Runtime 完成并交付正文 / Completed with a delivered answer | 10/10 | 10/10 | 10/10 | 6/10 |
| 预算内交付 / Delivered within the cumulative token cap | 10/10 | 10/10 | 10/10 | 5/10 |
| 必需评分项暂定通过 / Provisional required-item passes | 9/10 | 10/10 | 9/10 | 6/10 |
| 含显式来源绑定 / Answers with explicit source bindings | — | 8/10 | 10/10 | — |
| 模型 Token 合计 / Total model tokens | 8,911 | 16,512 | 16,091 | 262,482 |
| 平均在线耗时（秒） / Mean online duration (s) | 4.435 | 5.394 | 4.887 | 8.251 |

耗时含失败运行，所有 40 次均有用量和耗时记录。费用未记录。必需项分数与预算分开统计，完成且超预算的 Agent 回答仍可通过内容检查。评分为暂定，待团队交叉复核。与 [Qwen 试点](integration_validation_20261121.md) 对照时，Agent 的预算内交付从 10/10 变为 5/10。两批均为每题单次的开发试点。

Timing includes unsuccessful runs. Usage and duration are available for all 40 runs. Costs remain unreported. Content scoring is separate from budget compliance, so the completed Agent answer that exceeded the cap can pass its required content item. Scores are provisional pending cross-review. Against the [Qwen pilot](integration_validation_20261121.md), Agent delivery within budget changed from 10/10 to 5/10. Both batches are single-repetition development trials.

| 题目 / Task | 实际与复现 / Observation and reproduction | 交接 / Owner |
| --- | --- | --- |
| `pilot-002` | Agent 发起一次结构化澄清，取得允许披露的事实后未交付计算结果，累计用量 48,487 Token。查看 `run_20261121_e85d52` 的等待类型、披露次数和每次模型调用的 Token。 / Agent opened a structured wait and received the permitted fact, then terminated without delivering the calculation. Cumulative usage was 48,487 tokens. Inspect the wait type, disclosure count and per-call usage. | 成员 4 核对恢复后的预算和结束条件，成员 5 复测 / Member 4 checks resumed budgets and termination, Member 5 reruns |
| `pilot-004` | `run_20261121_92f3ec` 前两次模型调用累计 19,908 Token，第三次后合计 34,358，Runtime 仍返回 `completed`。`pilot-001/005/008` 也在调用结束后才跨过上限，并以预算耗尽结束。 / The first two calls used 19,908 tokens, and the third brought the total to 34,358. Runtime reported completion. Three other static tasks crossed the cap and ended as budget exhausted. | 成员 4 核对调用前预算和最终状态，成员 5 继续独立统计 / Member 4 checks pre-call budgeting and terminal status, Member 5 tracks cumulative usage |
| `pilot-005` | Direct LLM 和 Fixed Workflow 同时给出二十年规则与“第三年至第三十年”的错误区间。RAG 正确写出第二十一年至第三十年。核对三份正文及第 705 条引用。 / Direct LLM and Fixed Workflow state the twenty-year rule yet label years three to thirty as excess. RAG states years twenty-one to thirty. Compare the three outputs and their article 705 references. | 成员 5 保留回归题，成员 2、3 复核来源与表述 / Member 5 retains the regression case, Members 2/3 review the source and wording |
| `pilot-008` | 固定检索的前五仍缺标注预期条文。两个检索基线明确指出候选与交付争议的适用依据不足，同时保留材料中的数量差。 / The expected provision remains absent from the fixed top five. Both retrieval baselines flag the lack of applicable authority and preserve the quantity difference in the material. | 成员 2 核对召回，成员 3 复核依据适用 / Member 2 checks recall, Member 3 reviews applicability |

## 复现与结果 / Reproduction and results

使用本机配置中的 `DEEPSEEK_API_KEY`、`SILICONFLOW_API_KEY`、团队索引和受控隐藏事实文件。传入 `--prompt-key` 可在终端隐藏输入 DeepSeek 密钥。

Use local `DEEPSEEK_API_KEY` and `SILICONFLOW_API_KEY` values, the team index and the controlled hidden-facts file. Pass `--prompt-key` to enter the DeepSeek credential without echo.

```bash
.venv-evaluation/bin/python scripts/evaluation/run_evaluation_pilot.py \
  --four-mode --config evaluation/configs/consultation_pilot_deepseek.json \
  --public evaluation/datasets/pilot/tasks_extended.jsonl \
  --hidden evaluation/private/pilot_v2/hidden.jsonl
```

共享包包含运行状态、候选 ID、允许分享的静态题正文、暂定评分理由和清单。交互正文、披露内容和独立复核映射仅保存在本地。[共享结果目录](../../evaluation/results/20261121T094615Z-d64eba/README.md)

The shared package contains run states, candidate IDs, static answers, provisional review reasons and the manifest. Interactive answers, disclosed facts and the review-ID mapping remain local. [Shared results](../../evaluation/results/20261121T094615Z-d64eba/README.md)
