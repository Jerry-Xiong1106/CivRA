# 第7部分：个人维权流程与待办清单

版本：`civil_planning_v2`；2026-11-09。成员三提供用户案件业务流程与状态，不接管成员四的Agent调度。

## 已实现

- 保留原协商3步、调解3步、诉讼4步的`steps`，补充本案证据问题、原文对应关系及进度。
- `conditional_steps`单列保险核对（交通场景或存在insurance字段时）、履行核对、执行条件核对。它们不是新增的必经程序；执行节点不认定用户已具备申请条件。
- `edges`提供绘图数据，区分整理顺序提示与条件分支；具体机构、办理入口和法定期间未核实时不编造。
- 五种明确用户进度操作：待办、进行中、完成、受阻、用户标记不适用；可重新打开，旧版本保留。部分履行可以记录为进行中并附说明，具体履行金额仍通过既有事实接口登记。
- 沿用现有SQLite参考库和`CaseAssetStore`的不可变输出版本保存进度，无新数据库引擎、无CaseSnapshot结构迁移。
- 支持重启读取、原子版本检查、并发冲突拒绝、请求幂等和期限检查。每次状态更新需要真实已保存的用户消息、作者及连续原文片段。

## 三个Python入口

`CaseService.get_route_workflow`仍是原有工具；`get_workflow_progress`和`record_workflow_progress`为新增Python接口，暂未加入成员四的工具注册表。

### 1. 生成计划

```python
snapshot = repository.get_case(case_id)
plan = service.get_route_workflow({
    "context": {"request_id": "plan-v2-001", "case_id": case_id,
                "experiment_run_id": None, "deadline_at": None},
    "case_version": snapshot.case_version,
    "case_snapshot": snapshot,
    "route": "litigation",             # 必须与已明确选择的路线及stage一致
    "available_authorities": [],
    "progress_version": None,          # 读取并固定当前最新进度版本
    "source_notes": [],
    "dates": [],
})
```

计划保存后返回`output_refs[0]`，用于后续反馈。每次重新生成采用新`request_id`。若显式传`progress_version`，必须等于当前进度版本；不能用旧版本计划跳过新出现的待办。旧计划仍可通过输出存储读取，但不用于更新当前进度。

旧参数可继续使用，原基础步骤数量不变，历史空扩展参数摘要保持兼容。历史`civil_planning_v1`输出不会被改写；需要更新进度时先用新请求ID生成v2计划。

### 2. 保存实际反馈

成员1先通过应用入口保存真实用户回复，取得message_id和actor_id。该动作可能增加case_version，以下请求使用最新案件版本。仅新增一条回复不会使计划依据失效。

```python
current = repository.get_case(case_id)
saved = service.record_workflow_progress({
    "context": {"request_id": "progress-001", "case_id": case_id,
                "experiment_run_id": None, "deadline_at": None},
    "case_version": current.case_version,
    "route": "litigation",
    "expected_progress_version": plan.data.progress_version,
    "plan_output_id": plan.output_refs[0],
    "message_id": saved_user_message_id,
    "actor_id": authenticated_user_id,
    "quote": exact_quote_from_saved_message,
    "changes": [{"step_id": "jurisdiction", "status": "completed",
                 "note": "用户明确反馈已完成本项核对", "evidence_ids": []}],
})
```

status取值为`pending / in_progress / completed / blocked / not_applicable`。系统检查消息确实存在于本案、作者匹配、引文连续匹配、步骤属于已保存计划、证据未排除。**这不等于完成自由文本语义确认**：成员1的明确操作或成员4的确认流程必须保证状态选择与用户意思一致，不能拿无关消息冒充同意。

同批次最多30项且step_id不重复；任何一项非法则整批不写。相同request_id和参数重试返回原结果；不同参数复用ID拒绝。并发不同更新只允许一个写入，另一个返回`version_conflict`及预期/实际进度版本。该版本与案件case_version是两套编号。

进度保存在同一输出ID下的新output_version中，历史不可变。单路线参考实现最多保留1000个进度请求索引；超出需要另行实现归档，当前不会悄悄截断。Member1换用其他存储时需保留相同版本/原子写语义。

### 3. 读取进度并重新生成

```python
progress = service.get_workflow_progress({
    "context": context_with_new_request_id,
    "case_version": repository.get_case(case_id).case_version,
    "route": "litigation",
})
# 用新request_id再次调用get_route_workflow，读取新的revision。
```

`get_workflow_progress`返回原始历史状态。**页面判断当前能否跳过，要使用新计划中的status和should_skip**：完成/用户标记不适用只有在计划依据未变时才可跳过。事实、材料、诉求、目标、阶段或来源注释/日期输入变化时，旧记录仍可见，计划状态转为`needs_review`且should_skip=false。新增用户消息及时间戳本身不使依据失效。

不要把某个步骤completed解释为全部法律条件成立或案件已结束。工具不会自动切换路线、结案、发送材料或启动执行。

## 来源与日期

可选`source_notes`每项包含note_id、step_id、aspect、note、source_ref、quote，aspect为`contact/materials/conditions/deadline/next_action`。来源必须在available_authorities中且版本明确，步骤必须存在。通过第4部分来源核对后生成`source_checks`，并关联对应步骤的source_refs/source_note_ids。

“原文定位通过”与“候选说明适用于本案”分开：`semantic_support_checked=False`，机构、地址、期限等候选说明不会直接覆盖确定字段。失败项保留原因，其他节点仍可返回；真实超时不保存半成品。

可选日期示例：

```python
"dates": [{"step_id": "submit", "fact_id": "confirmed_target_date", "kind": "user_target"}]
```

kind为`user_target`或`material_record`，分别要求用户陈述或材料记录来源的日期事实；事实须有效、已确认、无冲突。它只把已知日期放入对应待办，不计算期限。日期用途由调用方指定，仍需确认；界面必须同时显示deadline_kind、deadline_fact_id及`deadline_verified=False`，不能把目标日或材料日期伪装成已核验的法定截止日。

## 对接与未完成部分

| 成员 | 需要对接 | 验收 |
|---|---|---|
| 1 | 任务图/清单、真实用户状态操作与消息、权限、进度历史及存储适配 | 只在should_skip为true时跳过；复核状态仍显示原记录；版本冲突后刷新 |
| 2 | 具体机构/入口、程序依据、法律时点、地域要求、期限规则及起算条件 | 精确来源可回查；原文核对与适用核验分开 |
| 4 | 理解进展、确认反馈含义、重新规划、追问、决定是否注册新增接口 | 不伪造用户确认，不自动认定保险责任或可执行性；新计划用新请求ID |
| 5 | 已履行/部分履行/受阻/变更/并发等独立样例 | 验证历史保留、旧计划失效、可选节点非必经、真实语义一致性 |

本次不包含实际渠道提交、现行法律期间计算、保险责任认定、执行条件的语义裁断或真实Agent自动重规划。完成的是可保存、可复核的业务待办链路。

## 验证

新增22项测试，包含进度生命周期、重启、真实消息引用、并发不同更新和同请求重试、旧案情/旧进度拒绝、来源和日期标识。联合演示14项检查通过。与第8部分合计新增44项测试；全量306项，288项通过，18项因本地缺少共享Runtime跳过。原40个交接场景通过。

```text
python -m unittest discover -s tests/case -p test_workflow_progress.py
python scripts/case/demo_workflow_outcomes.py --output <新的空目录>
```

设计核对参考[《保险法》官方文本](https://www.samr.gov.cn/zw/zfxxgk/fdzdgknr/bgt/art/2023/art_4c715a53f3d4402c89f62ae77809f638.html)和[最高法院执行程序须知](https://www.court.gov.cn/zixun/xiangqing/78732.html)，查阅日期2026-11-09。其中2018年办事说明仅用于识别需核验事项，没有转换为现行期限计算规则；本案办理要求仍需成员2按当前规则和地区核实。
