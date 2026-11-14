# 成员三第二批：案件更新、证据检查与诉讼目录

版本：`case-tools-0.2.0`；日期：2026-11-04。Python 3.11+，Pydantic 2，SQLite 使用 Python 标准库。

## 完成范围

| 能力 | 实现与验证入口 |
|---|---|
| 更新确认事实、证据处理状态与诉求 | `backend.case.update_case(request, repository)` / `CaseService.update_case()` |
| 保存历史、来源与变更原因 | `SQLiteCaseRepository.get_case(case_id, version)` / `get_history()` |
| 原子版本检查和幂等重试 | `expected_case_version`、`request_id`、数据库事务 |
| 缺失事实、日期/金额等单值冲突、完全相同文件、诉求材料缺口 | `backend.case.check_evidence(request)` |
| 诉讼证据目录、拟证明事项、补证待办 | `backend.case.build_evidence_directory(request)` |
| Runtime 实际工具注册与状态适配 | `backend.tools.case_tools.register_case_tools()`、`RepositoryCasePort` |
| 工具反馈导致追问、确认、更新、复查、路线选择和目录生成 | `scripts/case/demo_agent_business.py` |
| 正常/非法请求与实际结果 | `data/examples/member3/phase2_calls/` |

接口声明版本仍为 `0.1`，新增记录字段有默认值，第一批合成快照可读；实现版本为 `case-tools-0.2.0`。排除材料和解决冲突的约束有所增强，详见下文。新增目录工具是对 Proposal 证据目录能力的具体入口，需成员四/五在最终工具清单确认。

## 运行

在仓库根目录：

```powershell
python -m pip install -r requirements-case.txt
python scripts/case/demo_business.py
python -m unittest discover -s tests/case -v
```

默认 demo 使用临时数据库，执行完删除。保存数据库和完整结果时用一个全新位置：

```powershell
python scripts/case/demo_business.py --db var/member3-demo/cases.sqlite3 --output var/member3-demo/result.json
```

已有同 ID 案件不会被覆盖。`var/` 已被 Git 忽略。SQLite 案件数据、真实材料和运行轨迹不进入 Git。

与成员四分支 `104ca8a` 的代码集成后，安装其 `requirements.txt`，运行：

```powershell
python scripts/case/demo_agent_business.py
# 若需要留存轨迹，使用新的输出目录：
python scripts/case/demo_agent_business.py --output-dir var/member3-agent-demo
```

模型是 ScriptedLLM，业务检查、SQLite 写入、ToolRegistry 和 AgentRunner 都执行真实代码。脚本模型根据实际工具报告建立问题，业务工具不读取人工预期。固定用户回复只在 demo_support.py 中，后续真实应用由实际用户提供。

## 案件更新和数据库

`SQLiteCaseRepository` 是可替换的参考实现。应用、工具和评测应共用一个 repository；成员一可实现同样协议后替换它。

数据库有三张表：

- `case_heads`：每个案件的当前版本。
- `case_versions`：每个版本的完整快照、变更来源和审计记录。
- `case_requests`：成功更新的 request_id、请求摘要和原响应。

事务在同一连接内执行 `BEGIN IMMEDIATE`：比较当前版本，插入新快照、审计和幂等结果，再推进当前版本。并发写入同一旧版本只有一个成功。未通过全图校验或事务失败时不返回成功，旧版本不变。

相同案件、相同 request_id、相同变更重试，返回第一次的响应，不重复增加版本。相同 ID 改换内容返回 invalid_request。摘要不包含 deadline_at，允许重试时调整截止时间。原结果可能不是最新案件状态，应按需要重新 get_case()；幂等返回保留原版本是有意设计。

`update_case` 不改阶段和路线；用户路线确认动作通过 CaseStatePort 处理。更新只接受结构化事实、证据、诉求和应用保存的用户消息。已有消息不能重写；同 ID 事实的值和来源不能重写，新增事实应使用新 ID。原始证据内容与文件版本不能原地替换。

### 冲突解决与排除记录

- `Fact.superseded_by`：保留原值与材料位置，指向同字段、同值类型的已确认事实。仅新增一个确认值不会抹去其他冲突；显式解决关系后才停止把旧候选当作未解决冲突。
- `Evidence.exclusion`：`status=excluded` 必须有对应用户消息、确认人和时间。排除重复材料仍保留原记录，目录列明排除 ID；发现相同哈希不直接删除材料。
- 相同哈希仅说明登记的文件字节相同。上游文件存储必须在上传时对实际字节计算哈希，此批规则不重新读取任意文件校验哈希。
- 同一票据不会由本批自动求和。重复结果提示不得重复计入；损失项目计算在第三批实现。
- 确认记录验证来源关系，不解释自然语言是否确实同意。真实模型/应用仍要可靠提取确认语义；demo 的固定回复由演示适配函数显式解释，不冒充通用提取器。

## 证据检查规则与边界

`traffic_accident_v1` 开发规则检查事故日期、责任信息和保险信息；`repair_cost` 请求检查是否关联维修票据。这些是当前工程配置，不宣称是法定起诉条件、穷尽材料清单或案件胜诉条件。未知纠纷使用 generic_v1，进行通用一致性和链接检查。

- 缺失：无非空值、只有明确未知词或仅有 disputed 值时报告；未知保持 null，不自动写成未投保等结论。
- 冲突：同一单值 field 存在不同已知值，返回事实 ID、证据 ID、原文位置和问题；金额比较使用 Decimal，3000.00 与 3000.0 不算冲突，币种不同仍需处理。
- 重复：在未排除的证据中按 content_sha256 分组，输出相同文件组。扫描、裁剪、重新拍照或近似发票不在本批覆盖。
- 诉求材料：检查有效诉求是否链接可用事实、未排除材料及已配置材料类型，提出补充或说明问题。

返回 `insufficient` 表示本规则发现待办，`ok` 表示已执行规则没有发现问题，不表示材料真实、事实已获法院认定或法律证明力充分。问题带稳定 ID，便于追问和前端定位。工具不直接暂停运行，成员四根据建议问题调用 ask_user。

### 诉讼目录

必须 `stage=litigation` 且 `selected_route=litigation`。按输入顺序编号，输出文件 ID/版本、类型、位置、关联事实/诉求、拟证明事项及复核提示；发现冲突和缺口时仍返回目录草稿与补证清单。已排除材料另列，不加入有效目录。

拟证明事项是依据结构化链接生成的模板文字，不判断证据已经证明某事实。原始记载被 superseded 后仍在目录中保留差异提示。具体法院目录格式和法律依据待成员二提供后再适配。

## 对接成员四

```python
from backend.case import CaseService, SQLiteCaseRepository
from backend.case.runtime import RepositoryCasePort
from backend.tools.case_tools import register_case_tools
from backend.tools.registry import ToolRegistry

repository = SQLiteCaseRepository("var/cases.sqlite3")
service = CaseService(repository)
registry = register_case_tools(ToolRegistry(), service)
case_port = RepositoryCasePort(repository)
# AgentRunner(llm, registry, case_port, run_store)
```

`check_evidence` 和 `build_evidence_directory` 的模型参数为空，由可信 ToolContext 读取当前案件。`update_case` 参数包括 expected_case_version、changes 和 change_reason，工具禁止插入 upsert_statements；用户原始消息必须由应用记录。三个工具不能和相同名称的 Mock 同时注册。

应用收到真实用户回复后，先调用 `RepositoryCasePort.record_user_reply()` 保存消息，再使用新版本调用 resume_run。示例：

```python
saved = case_port.record_user_reply(
    case_id=case_id, expected_case_version=version, request_id="app-reply-001",
    message_id="msg-001", actor_id="authenticated-user-id", text=user_reply,
    created_at=original_received_at,
)
# 确认 saved.status == "ok"，再恢复 Agent。
info = runner.resume_run(run_id, reply, wait_id=wait_id,
    expected_case_version=saved.case_version, request_id="resume-001")
```

ToolResult.data 内保留完整 BusinessResult；实际业务内容为 data["data"]。问题来源同时转换为 Runtime 的 user_material SourceRef，使轨迹可以显示原始文件位置。法律 SourcePointer 仍需成员二的 source_resolver，不创建虚构法条。

`original_received_at` 是应用第一次收到该消息时记录的带时区时间；重试时复用同一个值，保证消息内容及请求摘要不变。

Runtime 的工作线程超时不会杀掉原线程。写入工具 max_retries=0，业务层在保存前检查 deadline，并以事务和 request_id 防重。遇到不明状态，应用先用原 ID 重试或查询版本。

## 对接成员一与成员五

| 成员 | 已提供 | 还需对接 |
|---|---|---|
| 成员一 | SQLite repository、CaseRepository/IdempotentCaseRepository、用户回复记录入口、历史查询 | 统一应用数据库和真实文件存取；在实际上传/回复流程计算哈希、建立可信消息记录；调用鉴权由应用负责 |
| 成员二 | SourcePointer 外键和 source_resolver 接口 | 法律原文与来源版本、正式法院模板；当前开发规则没有冒充法律依据 |
| 成员四 | 三个真实工具、CaseStatePort 适配、脚本模型联调 | 接入真实模型、把应用保存的消息/版本传进恢复流程；确认新增目录工具和字段 |
| 成员五 | 同步 Python service、正常/非法请求结果样例、问题与版本错误 | adapter 对接及独立标注复核；开发样例期望不作为最终测试答案 |

可重建样例：`python scripts/case/export_phase2_examples.py`。结果由真实工具执行产生，时间戳反映运行时刻。回归验证业务字段、版本关系、状态及引用，不要求运行时间戳字节相同。

## 验证记录

2026-11-04：把成员三代码放入成员四分支 `104ca8a` 的独立集成副本，运行已有 Runtime 测试和成员三测试，共 **94 项通过，无跳过**，另有 21 个参数化子检查通过。Python 3.12.14、Pydantic 2.14.1。包括持久化后重开、历史版本、并发旧版本写入、并发相同请求重试、等待事务后超时、原始记录不可覆写、确认与排除记录、用户回复重试，以及真实 ToolRegistry/AgentRunner 的追问恢复过程。

20 份实时生成的 JSON Schema 通过 Draft 2020-12 校验；三个实际请求/结果样例通过各自 Schema。Agent 演示最终状态 completed，案件版本从 1 到 4，保留四个历史版本。真实工具依次返回 check_evidence/insufficient、update_case/ok、check_evidence/ok、build_evidence_directory/ok。

当前成员三分支尚未包含成员四代码，单独运行 unittest 时相关集成测试会明确跳过；上述无跳过结果来自完整集成副本，不能把单分支的跳过当作联调通过。

## 未完成范围

OCR、自动材料字段提取、费用算法、路线比较、起诉状、语义引用核验和真实模型表现验证仍按第三批推进。参考数据库和接口还需成员一及使用方审核后统一集成，本地通过不表示队友已经确认。
