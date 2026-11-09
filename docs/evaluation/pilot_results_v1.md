# 首次Direct LLM / RAG试点结果 / First Direct LLM / RAG pilot

**中文**

日期：2026-11-09。数据：pilot-v0.2；协议：pilot-v0.3。批次：`20261109T104304Z-dd61ec`。

**English**

Date: 2026-11-09. Dataset: pilot-v0.2. Protocol: pilot-v0.3.
Batch: `20261109T104304Z-dd61ec`.

## Scope

**中文**

5个开发任务分别执行Direct LLM和RAG各一次，获得10份真实回答，全部完成，无截断或API失败。这说明两种基线已可调用，不代表总体法律准确性，也不代表四模式实验完成。

生成模型为`Qwen/Qwen2.5-7B-Instruct`，复用统一客户端，温度0，单次输出上限2000 Token。已通过鉴权模型列表确认可用，但未验证真实Agent工具调用；Agent适配仅用脚本回复测试。

RAG复用`RetrievalService`，使用exact-vector、metadata输入、top_k=5，每题仅以原始初始描述检索一次。本地新建的`BAAI/bge-m3`索引含现有1690条、1024维向量，索引和缓存被Git忽略。语料仅为已提供的三部法律及登记版本，不含完整类案库或完整法律覆盖。

**English**

Five development tasks ran once through Direct LLM and RAG, producing ten real
model responses. All ten executions completed without truncation or API failure.
This establishes a callable two-baseline path; it does not establish general
legal accuracy or complete the planned four-setting comparison.

Generation used `Qwen/Qwen2.5-7B-Instruct` through the existing shared model
client, temperature 0, and a 2,000-token output limit. The model was available in
an authenticated model-list query. Its tool-calling behavior has not been tested
against the real Agent; the Agent adapter was tested with scripted responses.

RAG used the existing RetrievalService with exact-vector routing, metadata
inputs, top_k=5, and one unchanged initial-input query per task. A new local
BAAI/bge-m3 index contains all 1,690 current provisions in 1,024 dimensions.
The index and embedding cache are ignored by Git. The current corpus is limited
to the three supplied laws and their registered versions; no reference-case
database or complete legal coverage is implied.

## Execution and usage

**中文**

| 指标 | Direct LLM | RAG |
| --- | ---: | ---: |
| 执行完成 | 5/5 | 5/5 |
| 模型调用 | 5 | 5 |
| 检索调用 | 0 | 5 |
| 输入Token | 1,535 | 6,632 |
| 输出Token | 838 | 1,238 |
| 模型Token总数 | 2,373 | 7,870 |
| 在线耗时总和 | 27.607秒 | 51.409秒 |
| 单题平均在线耗时 | 5.521秒 | 10.282秒 |
| 已包含的检索耗时 | 0秒 | 1.522秒 |
| 实际/估算费用 | 未知 | 未知 |

在线耗时不含构建索引。首次构建嵌入1690条文档，5个RAG查询也需要嵌入。客户端缓存成功结果并可能内部重试，但不提供计费Token或HTTP尝试总数。未配置价格版本，费用不能当作0。

**English**

| Measurement | Direct LLM | RAG |
| --- | ---: | ---: |
| Completed runs | 5/5 | 5/5 |
| Model calls | 5 | 5 |
| Retrieval calls | 0 | 5 |
| Input tokens | 1,535 | 6,632 |
| Output tokens | 838 | 1,238 |
| Total model tokens | 2,373 | 7,870 |
| Sum of online task durations | 27.607 s | 51.409 s |
| Mean online task duration | 5.521 s | 10.282 s |
| Retrieval time included above | 0 s | 1.522 s |
| Billed/estimated cost | Unknown | Unknown |

Index construction is excluded from online latency. Initial construction embedded
1,690 documents; the five RAG queries also required embeddings. The existing
embedding client caches successful results and may retry requests internally;
it does not expose billed usage or HTTP-attempt totals. No versioned prices were
configured, so neither model nor embedding cost is reported as zero.

## Provisional review

**中文**

下表按生成前写好的必需检查项评分，由助手查看输出及候选文本后暂定，不是盲化人工复核或独立法律核验。Gold仍为草稿，且本批没有在运行前冻结私有gold哈希；正式实验应通过控制/评分清单补上。

| 任务 | 必需项 | Direct LLM | RAG | 观察 |
| --- | --- | ---: | ---: | --- |
| pilot-001 | 已还本金与余额 | 2 | 2 | 都将10000元扣除已还2000元 |
| pilot-002 | 缺失还款信息 | 2 | 2 | 都指出缺口，没有猜出隐藏金额 |
| pilot-003 | 无依据年度统计值 | 2 | 2 | 都说明缺少官方统计出处，未编数字 |
| pilot-004 | 利息规则与支持 | 1 | 1 | 基本结论存在，但条件范围表述不精确 |
| pilot-005 | 租期与依据 | 0 | 2 | Direct年限矛盾且来源不同；RAG找到预期条文 |

窄检查项下，Direct通过3/5，RAG通过4/5，不能称为整份回答的法律正确率。分组结果为：静态任务Direct 2/4、RAG 3/4；交互设计题的条件性回答项两者均1/1。

pilot-002中两个基线都没有收到控制器补充回复，因此未测真实多轮信息获取。非必需的流程指导分数保留在本批评分记录，不改变成功数量。

**English**

Scores below apply to the required items written before generation. Reviews were
performed by the assistant after inspecting outputs and candidate texts. They
are not blinded human reviews or independent legal validation. Gold records are
still drafts. The private gold files did not receive a pre-run hash freeze;
formal experiments must add that step through a controller/scorer manifest.

| Task | Required item | Direct LLM | RAG | Observation |
| --- | --- | ---: | ---: | --- |
| pilot-001 | Documented repayment and balance | 2 | 2 | Both account for 2,000 repayment against 10,000 principal |
| pilot-002 | Missing repayment history | 2 | 2 | Both identify the missing facts instead of guessing the hidden amount |
| pilot-003 | Unsupported annual statistic | 2 | 2 | Both identify the missing official statistical source without inventing a number |
| pilot-004 | Interest-rule explanation and support | 1 | 1 | Both give the basic conclusion but introduce imprecise conditional wording |
| pilot-005 | Lease term and supporting provision | 0 | 2 | Direct output has inconsistent year boundaries and a different source; RAG locates the expected corpus provision |

Required-item success is 3/5 for Direct LLM and 4/5 for RAG under this narrow
checklist. These are not whole-answer legal-correctness rates. By task type:

- Static tasks: 2/4 Direct LLM; 3/4 RAG.
- Interactive-design task: 1/1 for each under its conditional-answer item.

Neither baseline received a controller reply on pilot-002. These results do not
measure actual multi-turn fact acquisition. Non-required guidance items are
retained in the batch review records; they do not alter task-success counts.

## Failures and diagnostic limits

**中文**

1. pilot-005中Direct一面说20年上限，一面把超出部分写成从第8年开始，并引用《合同法》第214条而非预期语料的《民法典》第705条。RAG找到705条并区分超出部分与整个租期。这只是一个诊断案例。
2. pilot-004中Direct引文混合了“未约定”和“约定不明”；RAG虽检索到680条，解释中仍出现过宽的自然人规则。均给部分分，说明检索不保证解释忠实。
3. 5份RAG回答均未输出要求的完整`[source_id]`标识。虽有自然语言条号，结构化引用要求仍未满足；单独报告，不暗中修补或事后改gold。
4. pilot-001将还款期限条款用于解释其本身不能确立的利息/本金推断。算术正确不代表每句法律分析有依据。
5. pilot-003检索到赔偿条款，但这些条款不能给出指定年度统计值；回答识别了缺口。候选召回与依据充分性要分开。

**English**

1. On pilot-005, Direct LLM says the excess starts at year eight while also
   stating a twenty-year limit. It cites Contract Law article 214 rather than
   the expected corpus Civil Code article 705. RAG retrieves article 705 and
   distinguishes the over-limit portion from the entire term. This is one
   diagnostic case, not proof of a general RAG improvement.
2. On pilot-004, Direct LLM's quote conflates absent and unclear interest terms.
   RAG retrieves article 680 but its explanation includes an overly broad
   natural-person statement. Both receive partial credit; retrieval alone did
   not ensure faithful explanation.
3. None of the five RAG answers emits the requested full `[source_id]` markers.
   Natural-language article references exist, but the structured-citation output
   requirement is unmet. This is reported separately, not silently repaired or
   retrospectively added to gold scoring.
4. On pilot-001, a repayment-timing provision is used to explain an
   interest-to-principal inference it does not itself establish. A correct
   arithmetic answer does not imply every legal sentence is supported.
5. On pilot-003, RAG returns related compensation provisions even though they
   cannot supply the requested annual statistic. The answer recognizes the
   gap. Candidate retrieval and evidence sufficiency must remain separate.

## Evidence and reproduction

**中文**

原始试点证据位于`evaluation/results/20261109T104304Z-dd61ec/`：

- `manifest.json`：配置、代码版本、脏工作区标记、实现/提示词/公开数据/语料/索引哈希。
- `pilot-*-*.json`、`records.json`：实际请求、回答、候选原文、模型标识、结束原因、请求ID、用量及时间。
- `review_scores.json`：逐项助手评分、理由和待人工复核状态。
- `score_summary.json`：按模式和任务类型汇总。
- `dependencies.txt`：隔离环境的依赖版本。

本批原始运行和限定字段的评分清单获准公开供组员查看；完整受控标注仍留在本地。查看结果无需密钥；重新生成仍需重建索引，当前完整评分命令仍需受控gold。复现命令见[模块说明](../../evaluation/README.md)。每次新运行单独建目录，本批未被覆盖，也未挑选最佳结果。

调用采用供应商的[聊天接口](https://docs.siliconflow.cn/docs/api/chat-completions-post)和[嵌入接口](https://docs.siliconflow.cn/docs/api/embeddings-post)。

**English**

Published development evidence is under:
`evaluation/results/20261109T104304Z-dd61ec/`.

- `manifest.json`: configuration, code revision, dirty-state flag, implementation,
  prompt, public-dataset, corpus, and index hashes.
- `pilot-*-*.json` and `records.json`: actual messages, answers, candidate texts,
  model identifiers, finish reasons, provider request IDs, usage, and timing.
- `review_scores.json`: item-level assistant scores, reasons, and pending human-review status.
- `score_summary.json`: aggregation by setting and task type.
- `dependencies.txt`: packages in the isolated execution environment.

This selected batch and a limited scoring checklist are approved for publication.
Complete controlled annotations remain private. Reading results needs no key;
regeneration needs index construction, and the current full scoring command
still needs controlled gold records. Use the
commands in the [module README](../../evaluation/README.md). Each new run gets
a new output directory; the first batch was not overwritten or best-of selected.

The implementation uses the provider's [chat](https://docs.siliconflow.cn/docs/api/chat-completions-post)
and [embedding](https://docs.siliconflow.cn/docs/api/embeddings-post) endpoints.

## Implementation checks

**中文**

最终本地测试为64项测试及4项子测试通过，`pip check`通过。5题三层数据有效，索引与当前语料哈希一致；源文件和运行JSON中未发现密钥。初次运行时私有标注、原始结果、索引和隔离环境均被Git忽略。后续根据用户授权仅发布本批结果和评分清单；私有标注、索引及环境继续忽略。

现已加入旧索引拦截：语料变化后要求先重建索引。首次批次早于此检查，但清单保留实际语料和索引哈希；后续源码变化没有改写首次回答或评分。

**English**

The final local suite passed 64 tests and 4 subtests. The installed environment
passed pip check. Five public/hidden/gold task records validated, and the index
matched the current corpus hash. Credential-pattern scans found no keys in source
or run JSON. At initial execution, private annotations, raw runs, indexes, and the environment
were ignored by Git. A later user authorization publishes this batch and its
limited scoring checklist only; private annotations, indexes, and the environment
remain ignored.

A stale-index guard now refuses execution after corpus changes until the index
is rebuilt. The first batch predates that guard; its manifest still records the
actual corpus and index hashes used. Later source changes do not alter retained
first-batch responses or review scores.

## Next work

**中文**

- 请成员2、3复核两项法律规则标注及来源版本。
- 在新版本运行中实现并测试引用标识约束，保留模型原文，区分标识有效与支持关系成立。
- 与成员4确认真实工具调用及共同检索配置后，再报告Agent效果。
- 接入真实证据/核验工具后，再声称Fixed Workflow完整。
- 扩展开发任务，再准备独立复核、隔离的最终测试。

后两题来自现有检索开发题，5题全部属于已见开发材料，不能转入未见最终测试。

**English**

- Review the first two legal-rule items and source versions with Members 2/3.
- Add and test citation-identifier enforcement in a separately versioned run,
  retaining raw model output and keeping identifier validity distinct from support.
- Confirm real tool-calling compatibility and shared retrieval configuration with
  Member 4 before reporting Agent results.
- Integrate real evidence/verification tools before claiming a complete Fixed Workflow.
- Expand development coverage, then prepare independently reviewed, isolated final tests.

The last two tasks derive from existing retrieval development questions. All
five are seen development material. Do not move them into an unseen final test.
