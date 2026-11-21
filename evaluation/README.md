# 实验评测模块 / Evaluation

**中文**

成员 5 负责实验协议、任务集、三个基线、运行器、评分及结果分析。Direct LLM、RAG、Fixed Workflow 和 CivRA Agent 已使用同一套运行器完成真实开发题试点。当前结果与集成问题见[集成报告](../docs/evaluation/integration_validation_20261121.md)。

**English**

Member 5 owns the evaluation protocol, task sets, three baselines, experiment runner, scoring, and result analysis. Direct LLM, RAG, Fixed Workflow, and CivRA Agent have run live development tasks through the same runner. See the [integration report](../docs/evaluation/integration_validation_20261121.md) for results and open issues.

## Deliverables

**中文**

- 明确系统能力、输入、交互规则、预算和失败处理的实验协议。
- 先建设约10个试点任务，再以约20个开发任务、40个最终测试任务为目标；试点始终属于开发数据。
- 复用统一模型与工具接口，实现Direct LLM、RAG、Fixed Workflow三个基线。
- 用统一运行器执行包括CivRA Agent在内的四种模式，保存输出、轨迹、用量、延迟、费用和失败。
- 提供任务评分表、盲化交叉评分记录、比较和失败分析。
- 保存版本化配置与复现说明。

**English**

- A protocol defining system capabilities, inputs, interaction rules, budgets, and failure handling.
- Approximately 10 pilot tasks, followed by a target of 20 development and 40 final-test tasks. Pilot tasks remain development data.
- Direct LLM, RAG, and Fixed Workflow baselines using shared model and tool interfaces.
- A runner that executes all four settings, including CivRA Agent, and saves outputs, traces, usage, latency, costs, and failures.
- Task-specific scoring checklists, blinded cross-review records, comparisons, and failure analysis.
- Versioned configurations and instructions sufficient to repeat an experiment.

## Layout

**中文**

| 路径 | 用途 |
| --- | --- |
| `schemas/` | 任务、输出、运行记录和评分格式 |
| `configs/` | 模型、模式、预算与实验配置 |
| `datasets/pilot/` | 可共享的开发输入与样例 |
| `baselines/` | Direct LLM 和 RAG，Fixed Workflow 复用带检索生成 |
| `adapters/` | 对接统一模型、检索、业务工具和Agent接口 |
| `mocks/` | 离线开发所需的确定性回复和工具 |
| `runners/` | 批量执行、交互控制、保存与失败记录 |
| `scoring/` | 评分处理、指标汇总与分析 |
| `../docs/evaluation/` | 协议、标注指南与评分规则 |
| `../tests/evaluation/` | 评测程序检查 |

**English**

| Path | Contents |
| --- | --- |
| `schemas/` | Task, output, run-record, and score formats |
| `configs/` | Model, setting, budget, and experiment configurations |
| `datasets/pilot/` | Shareable development inputs and examples |
| `baselines/` | Direct LLM and RAG, with retrieval-assisted generation reused by Fixed Workflow |
| `adapters/` | Integration with shared model, retrieval, business-tool, and Agent interfaces |
| `mocks/` | Deterministic responses and tools for offline development |
| `runners/` | Batch execution, interaction control, persistence, and failure recording |
| `scoring/` | Score processing, metric aggregation, and analysis |
| [`../docs/evaluation/`](../docs/evaluation/) | Protocol, annotation guide, and rubric |
| [`../tests/evaluation/`](../tests/evaluation/) | Evaluation-specific checks |

## Work sequence

**中文**

1. 制定任务格式、评分项和协议。
2. 建设并复核试点任务。
3. 用模拟能力验证加载、披露、失败处理与汇总。
4. 接入统一接口，实现三个基线。
5. 开展四模式试点，修正协议和标注问题。
6. 冻结最终测试配置，执行实验与交叉评分。
7. 报告效果、资源消耗、鲁棒性和失败案例。

模拟运行只能检查软件流程。下方英文区块中的离线校验命令需要Python及Pydantic 2.7+；第二条还需本地受控标注。参见[试点说明](datasets/pilot/README.md)。这些校验命令不调用模型或嵌入服务。

**English**

1. Draft task formats, scoring items, and the protocol.
2. Build and review the pilot tasks.
3. Use mocks to check loading, disclosure rules, failure handling, and result aggregation.
4. Integrate the shared interfaces and implement the three baselines.
5. Run the pilot against all four settings and resolve protocol or annotation problems.
6. Freeze the final-test configuration, run the experiments, and complete cross-review.
7. Report quality, resource consumption, robustness, and failure cases.

Mock runs check the evaluation software; they are not evidence of model performance. Offline task checks (Python with Pydantic 2.7+):

```bash
python scripts/evaluation/validate_evaluation_tasks.py
python scripts/evaluation/validate_evaluation_tasks.py --private-dir evaluation/private/pilot_v1
python -m unittest discover -s tests/evaluation -p 'test_*.py' -v
```

The second command requires local controlled annotations, which are not committed.
See [pilot task notes](datasets/pilot/README.md) for access and review status.
No command above calls a model or embedding service.

## Integration responsibilities

**中文**

| 负责人 | 所需交付 |
| --- | --- |
| 成员2 | 检索接口、来源引用、知识库版本和检索标注 |
| 成员3 | 业务工具，以及事实、证据、计算和文书标注 |
| 成员4 | 统一模型接口、完整Agent、运行接口和轨迹导出 |
| 成员5 | 基线、协议、任务格式、运行器、评分和分析 |
| 全员 | 交叉复核并解决标注分歧 |

临时适配保留在本模块；采用公共定义前，先记录并共同确认跨模块接口。

**English**

| Owner | Required contribution |
| --- | --- |
| Member 2 | Retrieval interface, source references, knowledge-base versions, and retrieval annotations |
| Member 3 | Business-tool interfaces and annotations for facts, evidence, calculations, and documents |
| Member 4 | Shared model interface, complete Agent, run interface, and trace export |
| Member 5 | Baselines, protocol, task formats, runner, scoring, and analysis |
| All members | Cross-review and resolution of annotation disagreements |

Keep provisional adapters local to this module. Document proposed cross-module contracts before adopting shared definitions.

## Data handling

**中文**

`datasets/pilot/`仅保存允许共享的开发资料。最终测试标准标注和隐藏事实应置于受控存储，与提示词及检索数据分离；仅按协议披露隐藏事实。

不提交密钥、可识别个人信息或未确认公开权限的材料。写入本地结果或受控数据前配置存储及Git忽略规则。每次实验记录代码、数据、提示词、模型、配置和知识库版本。

**English**

Only shareable development material belongs in `datasets/pilot/`. Keep final-test gold annotations and hidden facts in controlled storage, separate from prompts and searchable data. Disclose hidden facts only under the protocol.

Do not commit credentials, identifiable personal data, or materials without confirmed publication rights. Before generating local results or controlled data, configure their storage and Git ignore rules. Record code, dataset, prompt, model, configuration, and knowledge-base versions for every experiment.

## Documents

**中文**

- [实验协议](../docs/evaluation/protocol.md)
- [数据标注指南](../docs/evaluation/annotation_guide.md)
- [评分规则](../docs/evaluation/rubric.md)
- [接口需求草案](../docs/evaluation/interface_requirements.md)
- [历史预检报告](../docs/evaluation/pilot_preflight.md)
- [首次试点结果](../docs/evaluation/pilot_results_v1.md)
- [简短进度说明](../docs/evaluation/progress_summary.md)
- [共享开发试点结果](results/README.md)

**English**

- [Protocol](../docs/evaluation/protocol.md)
- [Annotation guide](../docs/evaluation/annotation_guide.md)
- [Scoring rubric](../docs/evaluation/rubric.md)
- [Interface requirements (draft)](../docs/evaluation/interface_requirements.md)
- [Pilot preflight](../docs/evaluation/pilot_preflight.md)
- [Shared development-pilot results](results/README.md)

## Run the pilot

**中文**

下方命令依次创建隔离环境、安装依赖、构建metadata索引、运行试点和执行测试。构建索引时通过进程环境提供`SILICONFLOW_API_KEY`，不要提交密钥；基线可用`--prompt-key`安全输入。

本地Python如需指定可信CA，可设置`SSL_CERT_FILE`；基线命令在安装certifi后会使用其证书。每次运行创建新的`evaluation/results/`批次目录，不覆盖旧结果。首次结果见[试点报告](../docs/evaluation/pilot_results_v1.md)。

评分汇总与模型执行分开：下方第二组命令使用显式填写的评分文件和本地受控gold文件。程序只校验并汇总已有分数，不自动判断法律正确性。命令及参数保持英文，直接使用下方代码块。

**English**

```bash
python3 -m venv .venv-evaluation
.venv-evaluation/bin/python -m pip install -r evaluation/requirements.txt
# Set SILICONFLOW_API_KEY in your shell for index construction; never commit it.
.venv-evaluation/bin/python scripts/rag/build_vector_index.py --variant metadata
.venv-evaluation/bin/python scripts/evaluation/run_evaluation_pilot.py --prompt-key
.venv-evaluation/bin/python -m pytest tests -q
```

The baseline CLI also accepts SILICONFLOW_API_KEY from its process environment.
Use a trusted CA bundle through SSL_CERT_FILE if required by the local Python
installation; the baseline CLI uses certifi when installed. Results receive a new
batch directory under evaluation/results/ and do not overwrite earlier runs.
The first batch report is [pilot_results_v1.md](../docs/evaluation/pilot_results_v1.md).

Review aggregation is separate from model execution:

```bash
.venv-evaluation/bin/python scripts/evaluation/review_evaluation_pilot.py \
  --batch-dir evaluation/results/<batch-id> \
  --gold evaluation/private/pilot_v1/gold.jsonl \
  --reviews evaluation/results/<batch-id>/review_scores.json
```

Scores are entered explicitly against predeclared items. The script validates and
aggregates them; it does not infer legal correctness from model output.
