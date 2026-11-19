# 成员三第一批：案件结构、接口与开发样例

版本：0.1；日期：2026-11-03。负责人：成员三。

历史记录：第一批交付时的范围如下。第二批现已实现案件更新、证据检查、诉讼目录和 SQLite 参考存储，最新调用与验证说明见 [第二批实现](phase2-implementation.md)。

本批已实现数据结构、结构校验、协议适配和公开合成样例。业务工具的输入输出已定义，业务算法留到下一批。本文是可运行的接口提案，尚不代表其他成员已经确认。

## 1. 本批交付与验收

| 内容 | 状态 | 入口 |
|---|---|---|
| 案件、事实、证据、诉求、文书/分析输出结构 | 已实现 | `backend/case/models.py` |
| 六个业务工具和案件更新的请求/返回结构 | 已定义，算法待实现 | `backend/case/contracts.py` |
| 案件结构和内部引用校验 | 已实现 | `validate_case_snapshot()` |
| Python 导入入口和实时 JSON Schema | 已实现 | `backend.case`、`get_contract_manifest()` |
| Runtime v0.5 的案件视图和 ToolResult 转换 | 已实现；需集成分支联调 | `backend/tools/case_results.py` |
| Evaluation v0.1 的外层调用结果转换 | 已实现；需成员五确认映射 | `to_evaluation_result()` |
| 合成材料、案情、非法输入、人工开发预期 | 已实现 | `data/examples/member3/traffic_accident_v1/` |
| 真实解析/OCR、证据检查、计算、文书与语义核验 | 本批未实现 | 下一批 |

首轮演示不需要 API Key、网络、前端或数据库。在仓库根目录运行：

```powershell
python -m pip install -r requirements.txt
python scripts/case/demo_contracts.py
python -m unittest discover -s tests/case -v
```

后端使用 Python 3.11+、Pydantic 2。运行依赖统一在 `requirements.txt`，测试依赖在 `requirements-dev.txt`，OCR 按需单独安装。

## 2. 为什么这样划分

- 业务结构先放在成员三负责的 `backend/case/`，其他模块从这里导入。暂不修改成员四的 `backend/common/schemas.py`；团队确认后可以统一导出，不需要复制第二套定义。
- 业务代码不导入 Agent、不连接数据库、不自行启动模型。成员一提供持久化和文件存取；成员四负责何时调用；成员五负责基线顺序与评分。
- 确认与来源是两个维度。文件写了某日期，不等于用户已经确认该日期；用户确认了事实，也不等于法院已经认定。
- 两个未确认日期互相冲突、两份证据内容相同，都是合法输入。结构校验不擅自消除冲突、排除证据或改变诉求金额。
- 业务状态与运行状态分开。业务结构没有模型消息、token 预算、wait_id、run_id 或 checkpoint，这些归 Runtime。

## 3. 关键结构

完整字段、必填性、枚举、嵌套结构以 Pydantic 模型及生成的 JSON Schema 为准：

```powershell
python scripts/case/demo_contracts.py --schema
```

| 结构 | 核心字段与含义 |
|---|---|
| `CaseSnapshot` | `schema_version`、`case_id`、`case_version`、`synthetic`、`dispute_type`、`jurisdiction`、`stage`、`selected_route`、`user_goals`、`statements`、`facts`、`evidence`、`claims`、创建/更新时间 |
| `Fact` | `fact_id`、业务字段名、值和类型、来源种类、确认状态、对话/证据/原文引用、确认记录 |
| `Evidence` | `evidence_id`、`file_id`、文件版本、名称/类型、SHA-256、原文位置、关联事实、复核状态和重复关联 |
| `Claim` | 请求类型、描述、金额、事实/证据/依据引用、用户确认状态；不代表法律上必然支持 |
| `MaterialRef` | 文件 ID/版本，页码/段落/字符区间及可选原文片段 |
| `SourcePointer` | `source_id`、`source_version`，引用成员二拥有的完整来源记录 |
| `Finding` | 问题类型、严重程度、字段/事实/证据/输出位置、依据和建议修正 |
| `BusinessOutput` | 输出 ID/版本、案件 ID/版本、类型、复核状态、文本/成果 ID/结构化数据、缺失项、问题、对应关系及模板版本 |

### 事实的来源与确认

`provenance`：`user_statement`、`document_record`、`derived`。

`confirmation_status`：`unconfirmed`、`confirmed`、`disputed`。

- 用户陈述必须关联 `statements` 中的消息。
- 文件记载必须关联证据和原文位置。
- 推导值必须关联 `derivation_ref`；该字段是计算/分析结果标识，不是推导已正确的证明。
- `confirmed` 必须有非空值和确认记录，记录对应消息、确认人和时间。需要从真实用户回复建立该记录，不能把模型猜测写成确认。
- 顶层 `field` 表示单值字段，例如 `accident_date`。多主体/多项目请用区分实体的字段名，例如 `parties.p1.name`、`losses.repair1.amount`。同一单值字段可以保留多个候选值，不能有互相矛盾的已确认值。
- 结构层只验证确认记录是否可追溯，不验证回复语义是否确实表达确认；这一点需要应用交互和下一批业务检查保证。

### 金额、时间、未知项和位置

- `Money = {"amount": "3000.00", "currency": "CNY"}`。金额只接受非负普通十进制字符串，拒绝 float、科学计数法和 NaN；内部算术使用 `Decimal`。本版用于损失和费用，退款/负数另增明确类型。
- 日期事实为 `YYYY-MM-DD`，校验真实日历日期；时间戳必须带时区，JSON 统一输出 UTC 的 `Z`。
- 未知金额/日期用 `null`；没有可提取的事实可以完全不添加 Fact，由业务检查按规则报告缺失。`[]` 表示该集合当前没有记录，不冒充未知值。
- 页码从 1 开始；字符区间从 0 开始、左闭右开，按 Unicode 字符计数。带页码时区间针对该页提取文本；无页码时针对整个文件的提取文本。段落引用的区间针对该段文本。必须保存所引用提取文本的版本。
- `file_version` 不等于 `case_version`。补充其他材料会改变案件版本，不应悄悄改变已有文件版本。
- 原文定位和结构校验只能提供追溯条件，不能证明证据真实或法律结论正确。

### 路线和阶段

阶段沿用 Runtime：`consultation`、`route_selection`、`negotiation`、`mediation`、`litigation`、`closed`。

路线沿用评测草案：`negotiation`、`mediation`、`litigation`。选择前为 `null`；进入某条路线时 `stage` 与 `selected_route` 一致。回到路线选择时清空 `selected_route`。结构层不能确认用户实际点击过路线，需要成员一/四保障。

### 版本与存储

案件版本为大于零的整数。`BusinessOutput.case_version` 保留生成时版本，`output.is_stale_for(snapshot)` 检测是否过期；当前采用保守策略，任何案件更新都使旧输出需要复核。细粒度依赖更新留到后续。

`update_case` 请求带 `expected_case_version`。本批只定义更新请求；下一批业务逻辑校验变更后，由成员一的 `CaseRepository.replace_case()` 原子比较版本并保存。版本冲突必须返回 `version_conflict`，不能覆盖他人更新。`CaseRepository` 和 `MaterialReader` 只有协议，不创建竞争存储。

## 4. Python 调用与工具契约

已经可调用的入口：

```python
from backend.case import validate_case_snapshot
from backend.tools.case_manifest import get_contract_manifest
from scripts.case.fixtures import load_development_case
from backend.case.materials.contracts import CheckEvidenceRequest
from backend.case.contracts import InvocationContext

case = load_development_case()  # 同时校验样例材料哈希、字符位置和引用片段
result = validate_case_snapshot(case.model_dump(mode="json"))
assert result.status == "ok"

context = InvocationContext(
    request_id="dev-001", case_id=case.case_id,
    experiment_run_id="development-only", deadline_at=None,
)
request = CheckEvidenceRequest(context=context, case_version=case.case_version, case_snapshot=case)
request_json = request.model_dump(mode="json")
schema = get_contract_manifest()["schemas"]["check_evidence_request"]
# request 校验和 schema 生成已经实现；真正的 check_evidence 算法在下一批。
```

同步 Python 协议在 `BusinessTools` 中定义，不强制 asyncio 或 HTTP。六个业务工具输入输出如下，名称保留成员五草案：

| 接口 | 请求 | 成功 data |
|---|---|---|
| `parse_documents` | 上下文、案件版本、文件 ID | 文档、提取文本与位置、候选事实、解析/OCR 警告 |
| `check_evidence` | 上下文、版本、案件快照 | 缺失事实、冲突、重复材料、建议问题 |
| `calculate_costs` | 上下文、版本、计算类型、规则版本、输入、依据引用 | 金额、公式、原输入、假设、依据、未解决参数 |
| `compare_routes` | 上下文、版本、快照、依据、计算结果、用户目标 | 可选路线、前提、步骤、成本、未知项及依据 |
| `draft_document` | 诉讼阶段快照、已确认事实 ID、模板 ID/版本、依据 | `BusinessOutput`，包含草稿/成果引用、缺失项及事实证据对应 |
| `verify_output` | 快照、候选输出、引用来源 | 分级问题、已执行检查、未执行检查 |
| `update_case` | 上下文、预期版本、结构化变更、变更原因 | 新快照、已变更事实/证据/诉求 ID |

工具返回 `BusinessResult[T]`：`schema_version`、`case_id`、`case_version`、`status`、`summary`、`data`、`source_refs`、`error`。

- `ok`/`empty`/`insufficient` 都是调用成功，仍保留案件版本和结构化 data。
- `error` 必须有 `BusinessError` 且 `data=null`。未知案件身份/版本只能出现在无法建立上下文的错误响应中。
- 上下文与快照的案件 ID、版本必须一致。快照内部的事实、证据和诉求引用不得悬空。
- 本批无内部重试、不做网络调用；适配函数不自动执行或重试工具。后续模型辅助操作需要复用成员四的统一模型接口并报告用量，不能偷偷增加未计数调用。

## 5. 对接成员二、四、五

### 来源引用：不重新定义 SourceRef

成员二拥有完整 `SourceRef`，但目前检索结果、Runtime v0.5 和 Evaluation v0.1 的字段尚不相同。本模块只存版本化外键 `SourcePointer`；用户材料用 `MaterialRef`。

向 Runtime 转换时，调用方提供 `source_resolver(pointer)`，从成员二的来源记录读取相同 ID/版本的完整来源，并映射为 Runtime v0.5 字段。转换拒绝缺失来源或静默换新版本。本批样例没有真实法律引用，不虚构法条或结论。

工具正式实现时，应把结果中所有对外引用归集到顶层 `BusinessResult.source_refs`，嵌套引用与顶层保持一致，便于成员四记录来源。原文读取和存在性检查仍由成员二提供。

### Runtime v0.5

`runtime_case_view(case)` 返回 `{case_id, case_version, stage, summary}`，与成员四现有 `CaseSnapshot` 兼容。`summary` 内保留完整业务字段，事实列表不压扁为覆盖冲突的字典。现有 Mock 仍使用旧事实字典，替换为真实工具时要一起对接，不能混用 Mock 的字段假设。

以下例子在接入成员四分支后运行：

```python
from backend.agent.case_adapter import runtime_case_view
from backend.tools.case_results import to_runtime_tool_result
from backend.agent.case_port import CaseSnapshot as RuntimeCaseSnapshot
from backend.common.schemas import ToolResult

runtime_case = RuntimeCaseSnapshot.model_validate(runtime_case_view(case))
tool_result = to_runtime_tool_result(result, result_type=ToolResult)
# 有来源时还必须传 source_resolver=...；result_type 不传时返回相同结构的 dict。
```

`ToolResult.data` 包含完整业务结果：实际业务内容在 `data["data"]`，同时保留业务版本。Runtime 的 status、summary、case_version 保持现有字段；没有修改成员四的公共类或注册表。

`version_conflict` 暂映射为 Runtime 已有的 `INVALID_ARGS`，原始码保留在 `error.details.business_code`。其他错误映射见 `RUNTIME_ERRORS`。建议后续由成员四决定是否新增专用版本冲突码。

### Evaluation v0.1

```python
from evaluation.adapters.case_results import to_evaluation_result

evaluation_result = to_evaluation_result(result, context, duration_ms=0, attempt_count=1)
```

外层 `status` 只有 `ok`/`error`，表示调用成功与否；`data` 内保留完整 `BusinessResult`，所以 `insufficient` 不会被丢失或误当执行失败。错误使用小写业务码，元数据保留耗时、实现版本和尝试次数。`duration_ms=0` 仅是上面示例的占位，实际测试由调用方计时。

`experiment_run_id` 允许 `null`，便于正常应用调用；评测调用必须由成员五提供其真实实验 ID。业务模块不读取隐藏事实或最终测试标注。

### 需要队友确认的事项

| 对接人 | 本版可运行提案 | 待确认事项 |
|---|---|---|
| 成员二 | 使用 SourcePointer，调用方解析完整来源 | SourceRef 最终字段、来源版本未知的处理、原文读取方式 |
| 成员四 | 保留 v0.5 ToolResult 和 CaseSnapshot 外形，适配内部业务结构 | summary 中 facts 改为列表、版本冲突映射、关键规则检查是否由流程强制触发 |
| 成员五 | 同步 Python 调用、外层结果+内层业务状态 | 适配路径、嵌套 data、context 可空实验 ID、哪些工具先接试点 |
| 成员一 | Repository/MaterialReader 协议 | 文件版本、确认记录、原子版本更新、成果存取接口 |

这些事项是接口协调，不等于已经获得其他成员确认。本批不自动发 Issue 或私信。

## 6. 合成样例与测试边界

`traffic_accident_v1` 包含：

- 用户记得日期为 2026-05-12，合成事故材料写 2026-05-13。
- 同一张 3000.00 元合成票据以两个文件 ID 上传，内容 SHA-256 相同。
- 用户尚未提供保险信息；尚未选择处理路线。
- 只有“示例甲”这一称呼有明确确认记录；日期和票据记载保持未确认。
- 一份引用不存在证据 ID 的非法快照。
- 一份人工编写的输出结构样例，以及独立的 `expected_review.json`。

材料为 UTF-8 文本，OCR/PDF 解析不在本批实现。哈希是实际文件字节的 SHA-256，字符区间可回查原文。

`expected_review.json` 是公开开发预期，不是最终测试标准答案，也不是工具已检测到的结果。样例加载和 demo 不读取它；它不被放进案件快照或 Agent 上下文。后续最终测试由成员五重新隔离标注。

重建样例：`python scripts/case/build_dev_fixture.py`。独立测试检查金额、时间、确认、引用、跨案/版本混用、状态一致性及适配。`test_runtime_compatibility.py` 在没有成员四代码时明确跳过；合并到集成副本后必须执行，不能把跳过当作兼容已验证。

### 本次验证记录

2026-11-03，Python 3.12.14、Pydantic 2.14.1。把本批代码复制到成员四分支 `104ca8a` 的临时集成副本，运行现有 Runtime 测试及本批测试：60 项通过、无跳过。其中包括 31 项原有 Runtime 测试、26 项结构/适配测试、3 项真实 Runtime 类型和注册调用测试。模型使用 ScriptedLLM，无真实 API 调用。

18 份生成的 JSON Schema 通过 Draft 2020-12 结构检查，案件快照和请求实例也通过对应 Schema 校验。上述结果证明本次固定版本的结构兼容与控制流程可运行；不代表业务算法、法律适用或后续分支版本已经验收。

## 7. 下一批

优先实现 `update_case` 和 `check_evidence`，再演示一次真实问题反馈导致追问与更新。之后补材料解析、计算、路线、文书和业务核验。本批不会返回伪造的“证据检查通过”或“草稿已生成”。
