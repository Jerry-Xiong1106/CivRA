# 试点预检：协议与任务格式 / Pilot preflight: protocol and task contracts

**中文**

日期：2026-11-09。协议：pilot-v0.2；数据：pilot-v0.1。

本文件是最初3题的历史预检快照。后续5题真实运行见[首次试点结果](pilot_results_v1.md)；下方模型未设置、付费执行关闭等状态仅指当时，不代表最新进度。

**English**

Date: 2026-11-09. Protocol: pilot-v0.2. Dataset: pilot-v0.1.

Historical preflight snapshot for the original three tasks. The later five-task
execution is recorded in [pilot_results_v1.md](pilot_results_v1.md); the model-null
and execution-disabled statements below describe the earlier snapshot only.

## Completed

**中文**

- 在协议和JSON配置中记录试点范围、基线流程、检索配置、预算、交互及复核门槛。
- 分别实现公开输入、隐藏事实和评分标注格式。
- 新增3个公开合成开发任务及本地受控标注。
- 提供离线JSONL校验器并导出3份JSON Schema。

**English**

- Recorded the first pilot's scope, baseline procedures, retrieval configuration,
  limits, interaction policy, and review gates in the protocol and JSON plan.
- Implemented separate public, hidden-fact, and gold-annotation contracts.
- Added three public synthetic development tasks and local-only controller/scorer records.
- Added an offline JSONL validator and exported three JSON Schema documents.

## Checks performed

**中文**

| 检查 | 当时结果 |
| --- | --- |
| 任务格式单元测试 | 15项通过 |
| 公开任务 | 3条有效 |
| 三层数据关联 | 3条匹配，2静态、1交互 |
| 标注复核 | 3份均为草稿，不用于正式法律评分 |
| 私有文件Git忽略 | hidden.jsonl、gold.jsonl均被忽略 |
| Git空白检查 | 通过 |

使用内置Python和Pydantic 2.14.2；不代表所有项目依赖或真实模型/检索集成都已测试。公开数据SHA-256保留在下方英文记录中。

测试覆盖私有字段拒绝、模型输入白名单、重复任务/评分/JSON键、空输入、错误试点划分、孤立标注、版本不一致、静态披露限制、最终测试复核门槛、同案跨集和不泄漏私有内容的汇总。

JSON Schema检查字段结构；Python还检查跨字段、跨文件规则。格式校验不等于端到端隔离验证，未来运行器仍需检查实际模型请求。

**English**

| Check | Result |
| --- | --- |
| Task-contract unit tests | 15 passed |
| Public task validation | 3 records valid |
| Public/hidden/gold bundle validation | 3 matched records; 2 static, 1 interactive |
| Annotation review status | All 3 draft; not approved for formal legal scoring |
| Private storage ignored by Git | hidden.jsonl and gold.jsonl both ignored |
| Git whitespace check | Passed |

Tests used the bundled Python runtime with Pydantic 2.14.2. This is not a claim
that all project dependencies or real model/retrieval integrations were tested.
The public dataset SHA-256 is
`ef82e7c0012a9125ebded0ba5cc3a7202a5152cabd7a7d940f0da542ad19d245`.

The tests cover private-field rejection, the model-input allowlist, duplicate
task/score/JSON keys, empty prompts, invalid pilot splits, orphan annotations,
version mismatch, static-task disclosure, final-test review requirements,
case-family split leakage, and private-free summaries.

JSON Schema exports describe field-level structure. Python validation additionally
enforces cross-field and cross-file rules. Use the Python validator for full checks.
These checks establish contract behavior, not complete end-to-end input isolation;
the future runner still needs an integration test inspecting actual model requests.

## Next execution gate

**中文**

当时下一步是实现基线和结果记录、验证共享检索、选择适合后续工具调用比较的硅基流动模型并记录配置。当时`model=null`、`paid_execution_enabled=false`，未调用模型或嵌入接口，未保存密钥，也未产生法律效果分数。

声称法律准确性前应复核标注。正式效果报告必须保留真实输出、引用、费用、失败和评分理由，不能把预检当作模型效果。

**English**

Implement the baseline runner and output records, verify shared retrieval access,
select one SiliconFlow model suitable for the later tool-calling comparison, and
record its configuration. The current plan has model=null and
paid_execution_enabled=false. No model or embedding request was made, no API
credential was persisted, and no legal-performance score was produced.

Review the draft legal annotations before claiming legal accuracy. A new
performance report must retain the actual outputs, citations, costs, failures,
and scoring reasons; it must not present the preflight checks as model results.
