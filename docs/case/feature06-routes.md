# 第6部分：协商、调解与诉讼路线比较

实现版本：`case-routes-0.2.0`，2026-11-09。成员三负责案件业务层。

## 本次结果与边界

沿用讨论稿补充的三路线、五维度：材料与证据、费用、时间与步骤、协议与履行、风险与取舍。三条路线并列展示，不选择路线、不改案件阶段、不调度Agent，也不以证据不足为理由自动禁止协商或调解。

原`compare_routes`直接展示传入费用的所有amounts，可能把`fee_exact`与`fee_estimate`重复列入，且没有重算。本次改为仅支持固定版本普通财产案件受理费的重算核对，展示一次`fee_estimate`。输入公式、金额、币种、来源版本、适用范围或日期不符时不采用；多组不同估算条件保留未知，不相加、不任选。材料损失和普通算术总额不作为路线支出。

没有新增调解收费规则、案件时间预测、法定期限推算、胜诉概率或路线评分。调解类型与机构尚未明确时，费用保持未知；不把“人民调解”规则泛化到所有调解和全部用户支出。工作流待办是第7部分，本次保留原模板与行为。

## Python调用

沿用`CaseService.compare_routes(request)`，原必填字段不变；增加两个可选输入，默认空列表。生成式JSON Schema自动包含扩展，工具总数仍是原来的十个。没有替成员四新增工具注册或修改共享Runtime。

```python
snapshot = repository.get_case(case_id)
request = {
    "context": {"request_id": "routes-new-001", "case_id": case_id,
                "experiment_run_id": None, "deadline_at": None},
    "case_version": snapshot.case_version,
    "case_snapshot": snapshot,
    "available_authorities": [],
    "calculations": [],        # calculate_costs返回的data，不是整个BusinessResult
    "user_goals": ["节省成本", "尽快解决"],
    "source_bindings": [],     # 可选，下面解释
    "user_expenses": [],       # 可选，下面解释
}
result = service.compare_routes(request)
```

通过CaseService调用会核对数据库中的案件内容与版本，保存不可变输出，返回`output_refs`；重复相同请求复用输出，复用ID但改变参数会拒绝。需要新版比较时使用新`request_id`。历史请求的空扩展字段不改变摘要，历史比较结果仍可读取，`implementation_version`为`civil_comparison_v1`；不会偷偷用新算法改写历史输出。

`backend.case.planning.routes.compare_routes(request, ...)`仍可独立调用，但只有模型校验，不读取案件库、不保存结果；实际应用优先用CaseService。

## 输出怎么显示

| 字段 | 含义与显示要求 |
|---|---|
| `options` | 固定三项，顺序为negotiation、mediation、litigation；不是优先级 |
| `options[].dimensions` | 五项，键为materials、costs、time_steps、agreement_performance、risks；含摘要、追问、事实/材料/问题/来源绑定ID |
| `case_context` | 有效已确认事实、待核对事实、未排除/已排除材料、有效诉求；附完整Finding、原文定位、证据规则版本与限制 |
| `case_context.findings` | 复用第3部分检查：缺失、冲突、重复、诉求关联和补证提醒；不是法定材料清单或采信判断 |
| `cost_items` | 有名称、用途限制、来源或事实ID的分项费用；显示这个字段优于无标签金额列表 |
| `costs` | 为兼容保留的cost_items金额投影；不能与cost_items重复相加，也不代表路线总成本 |
| `goal_focus` | 原样保留调用方和案件中的目标；明确词表映射关注维度，未知表达要求澄清，不作自由文本语义推断或排序 |
| `source_checks` | 每条候选陈述与原文的定位检查结果；语义支持未检查，不能显示为“法律结论已核验” |
| `cost_review_issues` / `unresolved_items` | 未采用费用的原因、来源失败、机构与期限等未知项；需要完整展示或提供展开入口 |

当前结果保留`status="insufficient"`，因为地区机构、法律适用、实际期限和总支出尚未齐备；这表示业务条件未齐，不表示调用失败。`status="error"`时检查`error.code`。没有新增静默“已完成”状态。

## 费用输入

`calculations`可传第5部分`property_case_acceptance_fee / cn_property_acceptance_fee_2007_v1`结果。程序重新执行固定规则，核对输入、精确值、展示值、公式与规则来源；展示时只列一次展示值。

- 同一组条件重复出现，合并显示并记录全部`calculation_indexes`。
- 不同请求金额、日期等构成不同方案，即使最终费用相同也不能自动选择。
- 有一组费用无法核实，即使另有有效候选也先保留未知，要求整理候选后再调用。
- 原`CalculationData`没有案件ID/版本或用户确认记录；重算能证明给定条件下算术一致，不能证明请求金额已获本案用户确认。条件会原样展示，成员四仍需确认本案金额、程序和日期。
- 费用条件、取整及特殊规则边界沿用[第5部分说明](feature05-costs.md)，未新增减免等规则。

其他支出需明确引用本案已确认的用户陈述金额事实：

```python
request["user_expenses"] = [
    {"route": "negotiation", "label": "用户自述交通支出", "fact_id": "confirmed_travel_expense"}
]
```

未确认、有冲突、已取代、不存在、非金额、非用户陈述的事实不会被计入。同一事实在同一路线仅列一次；在不同备选路线可以重复展示。路线归属/用途由调用方提供，仍需用户复核。不同事实可能仍表示同一支出，因此不自动汇总；不兑换货币，不将用户支出误记为官方收费。

## 原文来源绑定

仅传`available_authorities`并不表示这些来源支持所有路线。可逐条提交：

```python
pointer = {"source_id": "member2-procedure-rule", "source_version": "pinned-version"}
request["available_authorities"] = [pointer]
request["source_bindings"] = [{
    "binding_id": "mediation-condition-1",
    "route": "mediation",
    "dimension": "agreement_performance",
    "statement": "调用方提出的程序条件说明，等待语义与适用核验",
    "source_ref": pointer,
    "quote": "成员二返回原文中的连续片段",
    "conditions": ["调解类型、机构、时间及案件适用条件待核实"],
}]
```

`binding_id`唯一，来源需要明确版本且在`available_authorities`中。每组可选输入及原来源/计算/目标列表上限30项；引文和候选陈述最多2000字符。本版绑定仅接受法条/规则类型，案例事实不能直接充当普遍程序规则。

通过CaseService复用第4部分`review_sources`，依赖`source_resolver(pointer)`返回精确`source_id`、`version`、`source_type`、`title`、`locator`、`text`、可选URL/日期/地区元数据。只读对应版本，拒绝换最新版本。通过的卡片记录引文、Unicode字符起止位置、完整返回文本SHA256及元数据警告；短摘录和多版本问题保留。定位相对于读取器返回的text，不一定是完整法条。

`original_text_checked`只说明ID、版本、原文片段和定位已核对。`semantic_support_checked=False`始终保留：引文可能不能支持候选陈述，还需要核查完整条件和本案适用性。失败项目标为rejected，不进入路线source_refs；部分失败不会抹掉其他有效来源和三条路线。真实超时不保存半成品。

本地受理费源仍通过`CaseService.get_source`读取，仅支持该受理费计算；不能把它用作协商、调解、司法确认或其他程序结论的支持。来源字段只是可追溯引用，不是“全路线已核验”标志。

## 对接目录

| 对象 | 需要对方提供或实现 | 成员三现已提供 | 验收要点 |
|---|---|---|---|
| 成员1 | 比较页面、可展开的问题与原文、真实用户确认入口 | 三路线五维度、分项费用、待核实问题、来源卡片和输出ID | 未知不显示0，候选陈述不显示已核验；按钮确认后才执行既有选路更新 |
| 成员2 | 程序原文、精确版本、定位、效力时点、地区机构及适用条件 | 原文读取和绑定核对接口、部分失败与元数据警告 | 来源ID/版本/引文一致；不能使用不适用条款或案例代替通用规则 |
| 成员4 | 检索调度、用户目标理解、明确费用条件、候选陈述语义核验、追问及等待选择 | 原compare_routes扩展参数及结果，案件版本/幂等/超时保护 | 模型不假造确认、不自动选路；使用新版Schema接收新增字段 |
| 成员5 | 独立不同目标/缺口/错误来源/重复费用样例和预期 | 回归测试、合成演示、结构化结果 | 算术/来源定位/语义支持分别评估，跳过的Runtime检查不记为通过 |

## 验证和复现

```text
python -m unittest discover -s tests/case -p test_route_comparison.py
python scripts/case/demo_route_comparison.py --output <新的空目录>
```

新增26项测试，覆盖费用重复/篡改/混入损失、多费用候选、支出事实冲突、五维度及证据关联、来源精确版本/片段/部分失败/超时、目标映射、旧请求摘要和历史结果兼容。演示使用合成案件和本地受理费短引文，11项检查；不是独立真实法律效果评测。

全量回归262项：244项通过、18项因当前本地缺少共享Runtime而跳过，原40个工具交接场景通过。没有据此推断同学远程分支的完成情况。

## 官方资料核对记录

2026-11-09查阅讨论稿所引[《人民调解法》官方页面](https://www.beijing.gov.cn/zhengce/zhengcefagui/qtwj/201101/t20110101_781290.html)及[最高法院《诉讼费用交纳办法》](https://www.court.gov.cn/zixun/xiangqing/5092.html)，用于检查调解类型/收费项目需要区分以及受理费计算边界。本版没有把完整法规复制进模板，也未添加“所有调解免费”或确定办结时长等推断；真实程序来源仍由成员二提供，适用性由成员四组织核验。
