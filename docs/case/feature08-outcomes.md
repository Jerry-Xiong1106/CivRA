# 第8部分：可能结果与不确定性分析

版本：`case-outcomes-0.1.0`；2026-11-09。成员三实现按诉求组织与核对分析输入/输出的业务层，真实法律推理与语义审查仍由成员4模型、成员2来源及独立评审完成。

## 本次实现的能力

`CaseService.analyze_outcomes`是新增Python入口，暂未加入原十个Agent工具注册表。读取并核对当前案件快照，复用第3部分证据检查及第4部分来源核对；返回并保存逐项诉求分析、引用链和未完成检查。

它区分两类内容：

1. 无模型分析输入时，整理本案诉求、确认/待核对事实、材料和问题，提供“支持、部分支持、暂难判断”的**未应用条件分支框架**（template_not_applied）。不声称本案满足这些条件。
2. 收到成员4/用户提供的候选因素、类案差异和条件化结论后，核对ID、状态、来源及跨诉求引用，保留有利和不利方向；符合结构要求的候选仍标为candidate_needs_review或premises_pending，不变成已通过法律核验的结论。

不生成赔偿支持金额、责任比例评分、胜诉概率或模型调用费用；claim.amount仍是用户请求金额。没有额外执行LLM调用，也不会自动把材料多、事实已确认或引文存在解释为法院必然支持。

## 最小调用

```python
snapshot = repository.get_case(case_id)
result = service.analyze_outcomes({
    "context": {"request_id": "outcome-001", "case_id": case_id,
                "experiment_run_id": None, "deadline_at": None},
    "case_version": snapshot.case_version,
    "case_snapshot": snapshot,
    "claim_ids": None,           # 默认所有未撤回诉求；可明确指定1–30个ID
    "source_selections": [],
    "factors": [],
    "case_comparisons": [],
    "scenarios": [],
})
```

无有效诉求返回empty；有诉求时保留insufficient，因为实质审查尚未完成。未知、重复或已撤回的指定诉求会被拒绝。输出保存在现有资产库，返回output_refs；案件事实、金额、路线和阶段不改变。相同请求幂等，修改参数须换请求ID，旧案件版本拒绝。

## 完整候选分析输入

以下仅展示结构，示例文字不是法律依据。实际source_ref和quote须来自成员2的精确版本原文。

```python
request["source_selections"] = [{
    "selection_id": "rule-1", "expected_kind": "statute",
    "source_ref": {"source_id": actual_source_id, "source_version": actual_version},
    "quote": exact_contiguous_quote,
}]
request["factors"] = [{
    "factor_id": "factor-1", "claim_id": actual_claim_id,
    "polarity": "supporting",       # supporting / adverse / uncertain
    "description": "调用方提出的有利因素，仍待语义核验",
    "fact_ids": [actual_fact_id],
    "evidence_ids": [actual_evidence_id],
    "source_selection_ids": ["rule-1"],
}]
request["scenarios"] = [{
    "scenario_id": "scenario-1", "claim_id": actual_claim_id,
    "kind": "support",             # support / partial_support / not_supported / undetermined
    "statement": "如果以下条件成立，可进一步讨论本项请求获支持的情形",
    "conditions": ["需核实的事实和法律适用条件"],
    "factor_ids": ["factor-1"],
    "source_selection_ids": ["rule-1"],
}]
```

候选因素至少有一个明确引用；引用未知/已取代/来源已排除事实、已排除材料或失败来源时，列入rejected_candidates。引用有效但事实未确认或有冲突时保留为premises_pending。没有明确不利因素时会提示“尚未提供”，不会写成“无不利证据”。描述与有利/不利方向的语义支持尚未验证。

外部条件分支必须有条件文本，关联同一诉求的有效因素；支持、部分支持、不支持候选必须绑定至少一个已定位法律来源。类案不能代替法律来源。来源存在仍不足以证明条件成立，所有候选的semantic_support_checked保持false。默认框架始终保留并注明origin=template，外部候选origin=caller；不自动选出一个分支。

原文定位精确到版本、引文范围、返回文本SHA256和定位元数据。重复引文、短摘录、来源日期及多版本问题沿用第4部分警告。source_selections需要明确非空连续引文；失败来源只影响依赖它的候选，其他诉求和框架继续返回，真实超时不保存半成品。

## 类案差异

```python
request["case_comparisons"] = [{
    "comparison_id": "comparison-1", "claim_id": actual_claim_id,
    "source_selection_id": case_selection_id,   # 对应expected_kind="case"
    "fact_ids": [actual_fact_id],
    "similarities": ["调用方列出的相似事实"],
    "differences": ["责任主体、保险、证据或请求项目方面的差异"],
}]
```

系统核对来源确为案例、本案事实有效；法条不能充当案例。差异未说明、比较事实未确认时明确提示。案例事实是否真正相似、差异是否完整及裁判理由能否用于本案，仍待语义核验；semantic_comparison_checked始终为false。

## 返回结果与显示要求

| 字段 | 含义 |
|---|---|
| claims[].claim | 原诉求及状态；请求金额不是法院支持金额 |
| confirmed_facts / pending_facts | 原事实及来源；冲突、未知、未确认内容不会混入已成立前提 |
| available_evidence / excluded_evidence_ids | 材料与排除记录，不代表真实性或采信结论 |
| findings / finding_ids | 缺失、冲突、重复和关联问题及原文位置，按诉求关联并保留全案问题 |
| factors | 调用方的有利/不利/不确定因素，引用核对状态与限制 |
| case_comparisons | 类案原文、相似点与差异的候选整理 |
| scenarios | 未应用的框架分支及待审查的外部条件分支，禁止混为已认定结果 |
| rejected_candidates | 未采用候选的类型、ID及原因，不能静默忽略 |
| sources | 来源定位卡片；未绑定到分析内容的卡片不自动成为结论引用 |
| output_links | 已确认事实、未排除材料和已定位来源与诉求输出位置的对应关系 |
| checks_performed / checks_not_performed | 明确区分结构/引用检查与法律适用、语义支持、证明力、不利因素完整性等未做检查 |

输出source_refs只包含实际绑定到因素、条件分支或类案比较的来源。保存的BusinessOutput.links已接入既有verify_output，成员4可继续组织第10部分语义审查。**verify_output返回值才说明它本次执行了哪些审查；不能因新增这些链接就声称已经完成模型核验。**

接口限制：最多30项诉求、30个来源、60个因素、30个类案比较、30个外部条件分支。标识符在各组内唯一；template:前缀保留给默认框架。概率字段不在协议中；另拦截常见显式数值胜诉概率文本，但这不是完整语义审查的替代品。

## 对接与剩余工作

| 成员 | 对方需要提供 | 成员三提供/验收点 |
|---|---|---|
| 1 | 按诉求展开的结果页面、来源/材料回查和待核验标识 | 结构化分析与输出ID；请求金额不显示为预测赔偿，模板与候选分开展示 |
| 2 | 精确法条与案例原文、版本、法院日期、适用说明及真实差异资料 | 来源定位及类型检查；不能将检索命中等同结论成立 |
| 4 | 模型分析、因素和条件化候选、追问与核验循环、统一用量管理；决定工具注册 | 新Python入口、待核验状态、拒绝原因及verify_output引用链；无模型时只返回框架与缺口 |
| 5 | 独立有利/不利因素、法律适用、条件结果及类案差异标注 | 分别评价结构正确、事实忠实、语义支持和法律正确，不能只看返回非空 |

真实模型生成、证据真实性/证明力、法律适用、类案语义差异和不利因素完整性尚未完成。此版本完成的是成员三可独立开发的业务整理、引用约束、保存和核验衔接。

## 验证

新增22项测试，覆盖条件框架、双向因素、冲突前提、跨诉求误引用、来源失败、案例类型、概率字段拒绝、保存/重试/旧版本/超时及既有核验衔接。第7/8部分共44项新增测试，联合演示14项检查；全量306项中288项通过、18项Runtime相关检查跳过。

```text
python -m unittest discover -s tests/case -p test_outcomes.py
python scripts/case/demo_workflow_outcomes.py --output <新的空目录>
```

演示全部案例来源及候选分析均明确标为合成，只验证接口和状态链路，不作为真实法律正确性、真实用户语义或模型效果证据。
