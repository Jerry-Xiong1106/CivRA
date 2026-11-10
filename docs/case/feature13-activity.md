# 第13部分：业务调用进度与处理记录

更新：2026-11-09。新增由程序自动追加的业务工具开始/结束记录，供成员1展示、成员4关联完整运行、成员5定位评测结果。

## 记录哪些内容

`CaseToolClient.invoke`和`evaluate`现在自动记录其实际执行的10个共享业务工具调用。开始事件在工具执行前写入，结束事件在返回结果后写入；存入同一SQLite数据库的`case_tool_events`表。每次调用有独立`attempt_id`，同一请求重放也会留下新调用事件，但不会改变原有业务幂等结果。

记录案件与请求编号、实验编号、调用方提供的run_id/step_id、工具名称、参数摘要哈希、有限输入引用、输入/结果案件版本、结果/来源引用、业务状态、错误码、是否可重试、开始/结束时间及实际耗时。模型用量只保存业务结果明确提供的允许字段，未知为null或空字段，不能当零费用。

不保存完整参数、案情正文、提示词或模型思考过程。完整运行参数、模型步骤、检索步骤、预算、用户等待、检查点、取消和全程事件流仍由成员四的Runtime保存。本表是业务调用索引，不是完整Agent运行日志或任务完成百分比。

直接调用CaseService的Python方法不经过CaseToolClient，因此不会自动产生本表事件；新增7个辅助应用接口也没有擅自注册为模型工具。成员四将其接为工具时，应使用自己的统一运行记录或按本存储`begin/finish`约定适配。

## 运行关联与记录失败

原调用方式保持返回`BusinessResult`：

```python
client = CaseToolClient(service)
result = client.invoke("check_evidence", invocation)
```

推荐Runtime使用新入口，获得单独的记录回执：

```python
result, receipt = client.invoke_recorded(
    "check_evidence", invocation, run_id=current_run_id, step_id=current_step_id,
)
```

`run_id/step_id`由可信应用传入，不是允许模型任意填写的业务工具参数。不提供时保持null，不把experiment_run_id冒充运行ID。

- 开始记录写入失败：返回`tool_error`及`operation_started=false`，本次业务没有执行。
- 业务已经返回、结束记录失败：保留真实业务结果，回执`recording_failed=true`；开始记录仍可查询。**不能将此当成业务未执行而自动重试写操作。**旧`invoke`方式会发出RuntimeWarning，新入口可由UI明确提示记录未完成。
- 进程中断后只有开始事件：只能说明缺少结束记录，不能声称仍在运行、已经失败或已经成功；Runtime应按请求编号和已保存成果核对后决定恢复。
- 未知案件或无效案件上下文不写入事件表；应用层另行记录入口错误。新事件是追加写入，已保存事件不能覆写。

记录和业务写入不是同一个跨模块事务；开始事件能保留中断线索，不能承诺崩溃后一定有完整结束记录。

## 查询与分页

```python
case = service.repository.get_case(case_id)
page = service.get_tool_activity({
    "context": {"request_id": "activity-page-001", "case_id": case_id,
                "experiment_run_id": None, "deadline_at": None},
    "case_version": case.case_version,
    "after_sequence": 0, "limit": 100,
})
```

返回`events`、`through_sequence`与`next_after_sequence`。翻页保持上界；要刷新新事件则从上次最大序号开始且不指定上界。序号在数据库全局递增，查询始终按case_id隔离；最大每页200条。不要假设相邻案件事件序号连续。

事件的`insufficient`表示业务结果仍需补充，不等于调用崩溃；`error`才表示业务错误。前端可通过`output_refs/source_refs/input_refs`跳到结果与来源；未登记的资料仍需对应成员提供读取接口。界面必须注明这里只展示业务工具步骤。

## 存储替换、契约与验收

`CaseService(..., activity_store=store)`或`CaseToolClient(..., activity_sink=store)`支持注入。存储实现`begin(name, call, run_id, step_id)`、`finish(start, result, duration_ms)`、`query(request)`，并保证追加性、关联校验与案件隔离。自定义仓库没有SQLite连接且未注入记录存储时，工具可继续执行，`invoke_recorded`回执明确`activity_store_not_configured`；查询返回not_implemented。

业务实现版本更新为`case-tools-0.5.0`，公共契约仍为0.1，原10个工具名称不变。`get_contract_manifest()`新增`auxiliary_operations`和7项辅助接口的请求/结果Schema，均标明`registered_as_agent_tool=false`，便于成员5直接Python调用和校验。

| 成员 | 对接内容 |
| --- | --- |
| 1 | 受授权的记录查询、开始/结束配对、失败提示与成果/来源跳转 |
| 4 | 传入run_id/step_id、处理记录回执、接入完整运行/SSE/预算/检查点；模型轨迹不由本模块补造 |
| 5 | 用请求/实验编号关联结果、耗时、重试与失败；完整模型用量由Runtime统一导出 |

新增`test_tool_activity.py`共10项测试，包括并发调用、重放、重启、跨案查询隔离、参数正文不写日志、开始/结束存储失败。实现位于`activity.py`、`client.py`和CaseService包装。

联合演示：`python scripts/case/demo_review_resume.py --output output/review-resume-demo`。输出合成场景JSON、检查清单和独立演示数据库，覆盖错误计算→修正清单→重算→再核验→补充材料→重启恢复→调用记录；不是实际法律或模型效果评测。
