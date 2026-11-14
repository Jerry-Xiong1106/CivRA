# 功能1 案件建立与事实确认

更新日期：2026-11-09。成员三独立业务补全，模块版本 `case-intake-0.1.0`。

## 本次交付

- 读取同一案件、同一版本的事实、诉求、原始陈述和用户目标，生成纯文本与结构化案情核对摘要。
- 按当事人、日期事件、金额及其他字段组织事实，保留原文位置和来源引用。
- 显式区分已确认、待确认、有异议、未知、已被修正和来源已排除；相互冲突的候选不会静默合并。
- 集中列出待确认事实、现有规则发现的缺失和冲突。既有事实、版本、证据规则保持不变。
- 接收明确的结构化确认操作，准备现有 `UpdateCaseRequest`；调用方再通过 `CaseService.update_case` 保存。

本次没有注册新Agent工具，没有实现HTTP接口、前端、模型调用或自然语言确认器。原有十个工具的参数与注册保持不变。

## Python入口

```python
from backend.case.intake import CaseIntakeService, FactReviewRequest
from backend.case.contracts import CaseRequest, InvocationContext

intake = CaseIntakeService(repository)
context = InvocationContext(
    request_id="summary-001", case_id=case_id,
    experiment_run_id=None, deadline_at=None,
)
summary = intake.summarize(CaseRequest(
    context=context, case_version=current_case_version,
))
```

`summary.data` 包含：

| 字段 | 用途 |
|---|---|
| `facts` | 带原始Fact、显示值、分组、确认状态及冲突标志的列表 |
| `pending_items` | 缺失、冲突、未知、待确认及有异议事项 |
| `statements` | 原始用户陈述，保持来源，不自动升格为事实 |
| `claims` | 原始诉求及其状态，包括已撤回的记录 |
| `user_goals` | 已保存的用户目标 |
| `text` | 供核对的纯文本摘要，前端必须按文本渲染 |
| `limitations` | 该业务输出的覆盖范围与未执行能力 |

`status=insufficient` 仍会返回摘要，表示存在待核对事项。`status=ok` 仅表示本次已执行的整理规则没有产生待办，不表示法律分析完整。

## 四种核对动作

| `action` | 行为 |
|---|---|
| `confirm` | 确认指定候选事实的现有值，不改原文；未知值不能确认 |
| `defer` | 暂缓核对，保留原状态；不会撤销已有确认，也不会创建空更新 |
| `dispute` | 标为有异议，保留原值，旧确认仍可在历史版本查询 |
| `correct` | 以新事实ID和已保存用户消息创建确认后的替代值，原值保留并指向新事实 |

用户回复必须已由可信应用保存。模块验证消息属于当前案件且作者与 `reviewer_id` 一致，但不承担登录鉴权或解释消息原文。

```python
# message_id 来自应用已保存的真实用户回复。
review_context = context.model_copy(update={"request_id": "review-001"})
prepared = intake.prepare_review(FactReviewRequest(
    context=review_context,
    case_version=version_after_message_saved,
    message_id=message_id,
    reviewer_id=authenticated_user_id,
    actions=[{
        "fact_id": "f_date_user",
        "action": "confirm",
        "supersede_fact_ids": ["f_date_document"],
    }],
))
if prepared.status == "ok" and prepared.data.update_request is not None:
    saved = case_service.update_case(prepared.data.update_request)
    # 仅 saved.status == "ok" 后，才在页面或Agent状态中报告保存成功。
```

`prepare_review` 不写数据库。只暂缓或没有变化时，`update_request` 为 `None`。准备与保存之间发生版本变化时，由现有更新接口拒绝旧版本写入。

冲突仅按 `supersede_fact_ids` 显式处理。确认其中一个候选不会自动抹去其他值。`correct` 自动替换其 `fact_id` 对应的旧记录，其他候选必须另列；新值是用户陈述，不复制原文件引用来冒充文件原文。

重试保存应复用原来的 `update_request`，由现有幂等更新机制返回已保存结果。不要在案件已更新后重新解读同一消息并创建新的确认请求。

请求与输出均为Pydantic模型，可使用 `model_json_schema()` 导出当前结构。它们暂未加入成员四工具表或成员五实验适配。

## 按原图的进度

| 原节点 | 本次结果 |
|---|---|
| C 整理当事人、时间、金额和诉求 | 已补齐结构化摘要整理；自由文本理解与通用提取仍需成员四 |
| D 是否存在缺失或模糊信息 | 已有缺口检查，本次补齐统一待核对列表 |
| G 补充或修正事实 | 已有更新引擎，本次补齐明确核对动作到安全更新请求的转换 |
| H 保留待确认事项 | 已有事实状态，本次补齐可供展示的状态与待办输出 |
| I 用户核对案情摘要 | 已提供摘要与核对准备数据，页面由成员一实现 |
| J 保存案件记录与事实状态 | 继续复用已有存储、历史版本与幂等更新，未创建第二条写入路径 |

## 暂不实现的对接工作

| 对接方 | 需要对齐什么 | 你提供什么 |
|---|---|---|
| 成员1 | 案件创建和上传、用户权限、消息ID及作者、按版本读取摘要、确认按钮、错误反馈 | 摘要字段、四种结构化操作、准备结果和既有更新入口 |
| 成员4 | 纠纷分类、自由文本提取、追问顺序、语义确认、等待恢复、是否将新入口注册为工具 | 待核对事项、原始事实ID、来源、版本、稳定错误状态和演示 |
| 成员5 | 独立样例、状态与来源断言、错误确认和冲突解决评测 | 新增单元测试及合成业务演示；不把其直接作为隐藏测试答案 |

## 验证

```powershell
python -m unittest discover -s tests/case -p test_intake.py -v
python scripts/case/demo_intake.py
```

演示显式标为合成数据，调用真实存储和业务函数，不调用LLM，也不声称已经验证真实用户意图理解。

2026-11-09验证：新增18项测试全部通过；成员三本地回归共收集146项，128项通过，18项因未包含成员四Runtime代码而跳过。独立合成演示中，案件从版本1经消息保存和显式事实确认到版本3，仍保留其他待核对问题。跳过项未作为通过统计。
