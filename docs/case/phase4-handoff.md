# 成员三第四批接入与测试交接

实现版本为 case-tools-0.4.0，业务契约版本为 0.1。本批完成统一工具调用入口、Runtime 薄适配、四类场景样例、合成试点标注草案，以及 Agent 和固定流程的共用工具验证。CPU OCR 配置和第三批业务算法继续供这些入口调用。

## 原计划与交付

| 第四批任务 | 本批交付 | 接收成员 |
|---|---|---|
| 接入 ToolSpec、ToolContext、ToolResult | backend/tools/case_tools.py 绑定统一 CaseToolClient，十个工具共用参数定义和业务入口 | 成员四 |
| 提供正常、非法、缺失、冲突样例 | 十个工具各四个场景，共 40 个，可重建请求和实际响应 | 成员四、五 |
| 提供安装、版本和限制说明 | 本文、动态 get_contract_manifest 和可执行检查脚本 | 成员一、四、五 |
| 配合试点任务标注 | 10 份合成开发任务标注草案，列出给定事实、缺口、问题、可接受输出和禁止补造内容 | 成员五复核，法律内容另由成员二参与 |
| 验证 Agent 和 Fixed Workflow 共用业务函数 | 两个隔离案件分别运行真实 AgentRunner 与固定序列，并比较实际业务输出 | 成员四、五 |

本地开发和验证使用此前保存的成员四 Runtime 104ca8a。接收方与其当前代码组合后，运行本文命令确认兼容性。

## 安装与独立检查

已验证环境为 Windows、Python 3.12、Pydantic 2。普通材料解析和工具接入检查使用 requirements-case-dev.txt，其中包含业务依赖、pytest 和 jsonschema。

```powershell
python -m venv .venv-case
.\.venv-case\Scripts\python.exe -m pip install -r requirements-case-dev.txt
.\.venv-case\Scripts\python.exe scripts/case/check_handoff.py
.\.venv-case\Scripts\python.exe -m pytest tests/case -q
```

check_handoff.py 默认在临时目录完成 40 个场景并打印汇总。保存详细记录时使用一个新目录。

```powershell
.\.venv-case\Scripts\python.exe scripts/case/check_handoff.py --output-dir var/phase4-handoff-new
```

目录内包含 results.json、summary.json、contract-manifest.json 和各场景的隔离数据库。results.json 记录完整调用、实际评测响应及独立编写、待人工复核的预期检查。脚本遇到失败返回非零退出码。已有内容的结果目录会被拒绝，重复执行时换一个新目录。

这组文本场景可以独立运行。需要实际识别图片或扫描 PDF 时，按第三批说明安装 requirements-ocr.txt、准备模型缓存并运行 smoke_ocr.py。首次模型加载及下载应在正式实验前完成。

## 给成员五的 Python 入口

CaseToolClient 接受工具名称和 ToolInvocation。应用或评测控制器提供 context、case_version，arguments 存放工具业务参数。案件快照由服务读取，调用方可以通过 get_case_tool_contracts 查询每个工具的参数 Schema。

```python
from backend.case import (
    CaseService, CaseToolClient, SQLiteCaseRepository, ToolInvocation,
    get_case_tool_contracts, load_development_case,
)
from backend.case.contracts import InvocationContext

repo = SQLiteCaseRepository("var/phase4-client-new.sqlite3")
case = repo.create_case(load_development_case())
client = CaseToolClient(CaseService(repo))
request = ToolInvocation(
    context=InvocationContext(
        request_id="cost-call-001",
        case_id=case.case_id,
        experiment_run_id="experiment-001",
        deadline_at=None,
    ),
    case_version=case.case_version,
    arguments={
        "calculation_type": "sum_amounts",
        "rule_version": "arithmetic_v1",
        "inputs": {"amounts": [
            {"amount": "0.1", "currency": "CNY"},
            {"amount": "0.2", "currency": "CNY"},
        ]},
    },
)
response = client.evaluate("calculate_costs", request)
assert response["data"]["data"]["amounts"]["total"]["amount"] == "0.3"
parameters = get_case_tool_contracts()["calculate_costs"]["parameters"]
```

invoke 返回 BusinessResult，evaluate 在同一次业务调用外包装成员五约定的 request_id、status、data、error、meta。外层 status=ok 表示调用完成，内层 data.status=insufficient 表示业务条件仍有缺口。评测保留两层状态，并单独读取 checks_not_performed。

evaluate 要求有效的 InvocationContext，以便关联请求与实验。建议先构造 InvocationContext 和 ToolInvocation。对已提供合法上下文的原始字典调用，非法参数、版本和业务条件通过错误响应返回。上下文自身构造失败时，Pydantic 返回 ValidationError，调用应用负责处理该输入边界。

调用入口只执行一次，attempt_count 为 1。请求重试由调用方明确组织，变更请求复用原 request_id、原输入和原截止时间。工具按当前案件阶段开放，状态以数据库中的案件为准。更新工具的 expected_case_version 必须匹配调用版本，旧版本写入继续由事务和请求防重处理。

## 十个工具的参数

| 工具 | arguments 的主要字段 |
|---|---|
| parse_documents | file_ids，可选 file_versions、ocr_mode |
| check_evidence | 空对象 |
| calculate_costs | calculation_type、rule_version、inputs，可选 rule_refs |
| compare_routes | available_authorities、calculations、user_goals |
| get_route_workflow | route，可选 available_authorities |
| draft_document | route、confirmed_fact_ids、template_id、template_version、source_refs，可选 export_formats |
| verify_output | output_id，读取本案已保存输出 |
| update_case | expected_case_version、changes、change_reason，changes 含事实、证据、诉求变更 |
| build_evidence_directory | 空对象，诉讼阶段使用 |
| build_visualization | chart_type，取 timeline 或 evidence_graph |

用户回复先由应用通过 RepositoryCasePort.record_user_reply 保存，再引用消息 ID 确认事实。模型参数中的消息插入字段会被拒绝。应用按已有文件存取接口上传材料，输出通过 output_refs、artifact_id 取回。

原有 CaseService 和业务请求模型继续作为直接 Python 入口。CaseToolClient 在它们外层组织参数、上下文和版本检查，Agent 和固定调用使用同一个客户端执行业务函数。

## 给成员四的 Runtime 接入

```python
from backend.case import CaseService
from backend.tools.case_tools import register_case_tools

service = CaseService(repository, source_resolver=resolve_exact_source)
register_case_tools(registry, service, source_resolver=resolve_exact_source)
```

适配层把共享参数定义转为成员四 ToolSpec，把 ToolContext 转为 InvocationContext，并把业务响应转成 ToolResult。来源原文位置继续写入轨迹中的 user_material 引用。模型可见参数与固定流程参数共用同一套 Schema。

注册前检查同名工具，发现重复即返回错误，并保留原工具表。所有工具的自动重试次数为 0，写入工具标记 side_effects=True，包括保存输出的证据目录工具。Runtime 继续管理超时、工具线程、运行轨迹和主 Agent 调度。

ToolResult.data 是完整 BusinessResult，具体业务内容位于 ToolResult.data["data"]。业务 version_conflict 转成 Runtime INVALID_ARGS，同时保留 error.details.business_code。阶段限制可能在 Runtime 执行前返回 NOT_ALLOWED，固定客户端的阶段限制用 invalid_request 和 reason=stage_not_allowed 表达。

业务输出保存后若来源适配发生异常，错误响应保留 output_refs 和案件版本，应用可以查询已保存结果。响应仅记录异常类型。当前 ToolSpec 使用成员四默认的 30 秒工具时限，集成方可根据实测调整并记录配置。

## Agent 与固定流程验证

在包含成员四代码的集成副本中执行以下命令。

```powershell
python scripts/case/demo_shared_tools.py --output-dir var/phase4-shared-new
```

两个隔离案件从相同合成事实开始，分别执行材料解析、缺口检查、保存固定用户回复、案件更新、复查、金额计算、路线比较、用户选择诉讼、流程、目录、草稿、核验和时间线。

Agent 使用真实 AgentRunner、ToolRegistry、CaseStatePort 和脚本模型，实际根据工具报告形成追问并暂停恢复。固定流程通过 CaseToolClient.evaluate 按明确顺序调用相同业务。每种模式执行 11 次业务调用，覆盖十个工具，案件版本均从 1 到 4。

比较保留事实值、金额、来源位置、状态和版本，忽略隔离案件各自的 ID、产物标识、收件及确认时间、请求摘要。脚本保存两个模式的完整实际结果和比较汇总。这项检查验证工具接入一致性，真实模型的行为与任务质量留给成员四、五的后续实验。

## 给成员五的场景和标注

data/examples/member3/phase4/scenarios.json 包含 40 个开发场景。四类场景各十个，覆盖完整工具清单。部分冲突场景验证保留两种候选记载，预期仍可为 ok。正常调用也可能返回 insufficient，例如缺少来源的通用路线比较。

pilot_annotations.json 包含 10 份开发标注草案，各自关联一个可重建场景，统一标记 pending_member5_review。给定事实、缺口、证据或版本问题、可接受输出和禁止补造项由本批根据合成材料独立编写，待成员五人工复核。标注位于独立文件中，调用工具时只传入实际参数。成员五据此复核、调整自己的任务格式与评分规则，并另行组织独立测试集和法律正确性评审。

## 保留的选型与对接事项

| 事项 | 当前状态 | 后续对接 |
|---|---|---|
| 财产案件受理费规则及特殊情形 | 用户尚待确定首版范围，当前使用已有明确算术与材料汇总 | 用户、成员二 |
| Word、PDF 导出范围 | 当前可输出 Markdown，格式选择保留 | 用户、成员一 |
| 三类法院模板原表 | 已记录入口，附件尚待取得和逐项校对 | 成员二及模板来源 |
| 起草和语义核验的真实 LLM | 已有网关接口与用量字段，实际模型仍待联调 | 成员四，法律原文由成员二提供 |
| 试点标注审定与独立评分 | 10 份合成开发标注草案已提供 | 成员五 |

本批仍使用已有可编辑填写草稿和明确未执行项的核验结果。开发场景的通过情况按接入检查记录，法律正确性、手写准确率和真实模型任务成功率由相应实验评估。

## 本地验证记录

2026-11-05，保存的 Runtime 104ca8a 与本批代码组合后，159 项测试通过，无跳过，另有 67 个子检查通过。40 个交接场景全部通过。25 份领域及调用 Schema、10 份工具参数 Schema 通过结构检查。

Agent 与固定流程各执行 11 次实际业务调用，覆盖十个工具，最终案件版本均为 4。忽略各自标识和时间后的业务结果与案件记录一致。Agent 决策使用 ScriptedLLM，业务函数、运行控制、数据库、参数校验和输出存储均实际执行。完整请求和响应样例位于 data/examples/member3/phase4/call_examples.json。
