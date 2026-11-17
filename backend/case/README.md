# 案件业务层

应用业务操作以 `CaseService` 为主要入口；各功能的请求和返回模型与实现放在同一目录。底层实现供内部组合和单元测试直接使用。

## 目录与职责

```text
backend/case/
├── service.py          # 显式装配依赖、对外委托业务操作
├── models.py           # 案件、事实、证据、诉求和成果等核心模型
├── contracts.py        # 共用请求上下文、版本约束、审计和仓库协议
├── operations.py       # 业务请求/结果类型索引、业务阶段限制
├── case_context.py     # 有效事实、未知值判断、案件依据汇总
├── business_support.py # 期限、摘要、去重和错误结果等小型辅助函数
├── errors.py           # 与具体数据库无关的业务异常
├── intake/             # 案情摘要、确认准备、更新、结构校验、版本历史
├── materials/          # 材料读取、OCR、字段提取、证据检查及规则
├── calculation/        # 金额计算、票据汇总、费用规则和借贷计算
├── planning/           # 路线比较、办理步骤与进度
├── drafting/           # 文书草稿、官方原表、模板和文件导出
├── review/             # 回答引用检查、成果核验、修正任务
├── research/           # 来源读取与核对、条件化结果分析
├── storage/            # SQLite 案件、文件、成果及活动记录；成果保存与重放
└── presentation/       # 时间线、关系图和统计数据
```

各组的 `contracts.py` 保存该组请求与结果模型；更细的路线、流程模型仍放在对应组。根 `contracts.py` 不再聚集所有功能模型。`operations.py` 是原有业务契约索引，不注册或执行 Agent 工具。

- 材料读取在 `materials/documents.py`，文书生成在 `drafting/documents.py`。
- `review/answer_review.py` 检查回答引用，`review/verification.py` 核验已保存成果。
- `intake/history.py` 查询案件版本和延续信息；`storage/activity.py` 记录业务调用。
- `planning/workflow.py` 管理用户办理步骤；Agent 执行循环在 `backend/agent`。

## 正式调用与适配边界

```text
应用 → CaseService → 对应功能模块
AgentRunner → 原生工具 → CaseToolClient → CaseService → 对应功能模块
AgentRunner → RepositoryCasePort → 案件读取 / 可信用户消息与路线变更
```

`CaseService` 提供证据检查、材料解析、更新、计算、规划、文书、核验等业务入口，另提供 `summarize_case`、`prepare_fact_review` 作为案情整理入口。构造函数显式连接具体服务；默认 OCR 配置由 `materials/ocr.py` 提供，创建时不加载或下载模型。

接入代码的当前位置：

| 能力 | 模块 |
| --- | --- |
| Agent 案件端口与案件视图 | `backend/agent/case_adapter.py` |
| 工具调用校验、请求构造、业务活动关联 | `backend/tools/case_client.py` |
| 调用方参数投影、ToolInvocation | `backend/tools/case_contracts.py` |
| 原生案件工具 | `backend/tools/case_tools.py` |
| 工具结果及来源转换 | `backend/tools/case_results.py` |
| 可选官方原表工具适配 | `backend/tools/official_forms.py` |
| 完整契约导出 | `backend/tools/case_manifest.py` |
| 评测结果外层格式 | `evaluation/adapters/case_results.py` |
| 合成开发案件加载 | `scripts/case/fixtures.py` |

案件业务实现不导入 Agent 或工具实现。`case/__init__.py` 按需提供常用公开名称及历史兼容名称；只导入核心模型不会加载服务、OCR、文书或 Agent。仓库内旧子模块导入路径已更新，外部调用方需按新路径调整；没有为每个旧文件保留转发模块。

## 存储、版本与幂等

默认存储明确为 `SQLiteCaseRepository`、`SQLiteAssetStore` 和 `SQLiteActivityStore`，共用 SQLite 数据库。存储实现通过公共 `repository.connection(write=True)` 获得事务连接，保持提交、回滚和版本检查；业务模块不直接操作连接。非 SQLite 仓库必须显式注入适配的资产存储，活动存储可单独注入。

`storage/outputs.py` 管理基于快照的成果保存和幂等重放。案件变更的请求摘要、事务与审计仍在 `intake/updates.py` 和仓库；文书及官方原表需要原子保存导出文件和成果，保留各自的保存流程。不同类型的请求摘要不强行统一，避免破坏历史幂等记录。

`case.stage`、`selected_route`、运行检查点字段以及原数据库表结构沿用现有语义。本次没有迁移已有数据库或重写 `var/` 中的历史案件与运行结果。

## 调用示例

```python
from backend.case import CaseService, SQLiteCaseRepository
from backend.case.contracts import InvocationContext
from backend.case.materials.contracts import CheckEvidenceRequest
from scripts.case.fixtures import load_development_case

repository = SQLiteCaseRepository("var/cases.sqlite3")
snapshot = repository.create_case(load_development_case())  # 已有案件不会被覆盖
service = CaseService(repository)
result = service.check_evidence(CheckEvidenceRequest(
    context=InvocationContext(
        request_id="check-1", case_id=snapshot.case_id,
        experiment_run_id=None, deadline_at=None,
    ),
    case_version=snapshot.case_version,
    case_snapshot=snapshot,
))
print(result.status, result.summary)
```

## 验证与历史文档

```powershell
.\.venv-rag\Scripts\python.exe -B -m pytest tests -q -p no:cacheprovider
```

包依赖、非 SQLite 注入和事务回滚的回归检查在 `tests/case/test_package_boundaries.py`；其余功能测试继续按原业务场景组织。完整测试索引见 [tests/README.md](../../tests/README.md)。

历史业务设计及功能说明保留在 [docs/case](../../docs/case/)，目录与入口以本页为准；业务请求与结果 JSON Schema 由 `get_contract_manifest()` 实时导出。本次整理前后完整契约逐项一致。
