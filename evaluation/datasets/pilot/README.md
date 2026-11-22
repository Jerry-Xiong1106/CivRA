# 试点任务集 / Pilot task sets

`tasks_extended.jsonl`（v0.3）包含以下 5 题和 5 道新增开发题。本地隐藏事实和评分草案位于 `evaluation/private/pilot_v2/`。运行配置及复核方法见[集成报告](../../../docs/evaluation/integration_validation_20261121.md)。

`tasks_extended.jsonl` (v0.3) contains the five scenarios below and five additional development scenarios. The local hidden facts and draft annotations are in `evaluation/private/pilot_v2/`. See the [integration report](../../../docs/evaluation/integration_validation_20261121.md) for configuration and review.

**中文**

`tasks.jsonl`包含5个开发任务：

| 任务 | 类型 | 目的 |
| --- | --- | --- |
| pilot-001 | 静态 | 完整本金/还款材料及限定范围的咨询 |
| pilot-002 | 交互设计 | 缺失还款事实及受控追问 |
| pilot-003 | 静态 | 当前语料缺少所需官方统计资料 |
| pilot-004 | 静态 | 由rag-dev-012改写的利息规则咨询 |
| pilot-005 | 静态 | 由rag-dev-014改写的租期咨询 |

只能把公开案件输入发送给模型。`PublicTask.to_model_input()`排除任务标签、来源说明及标注元数据。任务正文为中文，字段名保持英文，说明文档提供中英对照。

隐藏事实与评分标注保存在本地`evaluation/private/pilot_v1/`，不在本目录中且被Git忽略。全新克隆不含这些文件；如需开发交互控制或评分，请通过约定的受控渠道获取，不能从模型输出反推缺失事实。

5份gold均为草稿，后两题有现有语料引用，但没有团队核验的法律标注。检查覆盖算术、不确定性、无依据主张及两项暂定规则解释；报告法律准确性前仍需复核。这些是可用于调试的开发任务，不是未见最终测试。

下方命令从仓库根目录运行，需要Pydantic 2.7+。第一条只检查公开任务，第二条检查三层数据的任务ID关联、版本、静态任务披露限制、复核状态及同案划分。输出只有数量和公开数据哈希，不显示隐藏事实或答案，也不调用模型/嵌入接口。

后两题已经是看过的开发材料，其案件族不能进入未见最终测试集。首次运行及限制见`docs/evaluation/pilot_results_v1.md`。

**English**

Five development scenarios are supplied in `tasks.jsonl`:

| Task | Type | Purpose |
| --- | --- | --- |
| pilot-001 | Static | Complete principal/repayment inputs and scoped consultation |
| pilot-002 | Interactive | Missing repayment history and controlled clarification |
| pilot-003 | Static | Requested official statistic absent from the current corpus |
| pilot-004 | Static | Interest-rule consultation adapted from rag-dev-012 |
| pilot-005 | Static | Lease-term consultation adapted from rag-dev-014 |

Only the public case packet may be sent to a model. `PublicTask.to_model_input()`
excludes task tags, provenance, and annotation metadata. Task content is Chinese;
field names are English and documentation is bilingual.

Local hidden facts and scoring records are in `evaluation/private/pilot_v1/`,
outside this directory and ignored by Git. They were prepared for this local
development iteration. They are not available in a fresh checkout; obtain them
through the agreed controlled channel for controller/scoring development.
Do not recover missing private records by guessing facts from model outputs.

All five gold records are drafts. The last two contain existing corpus references, but no team-verified legal annotations.
Checks cover arithmetic, uncertainty, unsupported claims, and two provisional rule explanations. They
must be reviewed before reporting substantive legal accuracy. These tasks are
not unseen final tests and may be used for implementation debugging.

From the repository root, using a Python environment with Pydantic 2.7+:

```bash
python scripts/evaluation/validate_evaluation_tasks.py
python scripts/evaluation/validate_evaluation_tasks.py --private-dir evaluation/private/pilot_v1
python -m unittest discover -s tests/evaluation -p 'test_*.py' -v
```

The first command validates public records only. The second also checks exact
task-ID joins, versions, static-task disclosure rules, review status, and
case-family isolation within the supplied manifest. It prints only counts and
the public dataset hash, not hidden facts or gold answers. None of these
commands calls a model or embedding endpoint.

The two adapted questions are already-seen development material. Their case-family
identifiers must stay out of any unseen final-test split. The first baseline run
and its limits are recorded in docs/evaluation/pilot_results_v1.md.
