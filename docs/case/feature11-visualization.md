# 第11部分：时间线、证据关系与案例样本统计

更新：2026-11-09；本轮完成成员三数据接口，真实前端绘图和真实案例标注仍待成员1/2/5接入。

## 当前案件图

沿用`CaseService.build_visualization`及原`chart_type="timeline"|"evidence_graph"`。时间线新增有效状态、冲突问题编号、证据与来源链接；未知日期单独计入缺失，不编造日期。仍保留已取代和未确认记录供用户辨认。

证据图增加材料直接关联请求、证据所声明的证明关系、重复材料关系、事实取代关系，并保留值、状态、原文位置。边标记`requires_review`及固定`legally_proven=false`，不能把连接线解释为法律上已经证明。原有记录及节点ID格式保持兼容。

## 案例统计接口

```python
case = service.repository.get_case(case_id)
result = service.build_case_statistics({
    "context": {"request_id": "stats-001", "case_id": case_id,
                "experiment_run_id": None, "deadline_at": None},
    "case_version": case.case_version, "case_snapshot": case,
    "selections": [{"source_ref": {"source_id": "case-a", "source_version": "v1"},
                    "expected_kind": "case"}],
    "group_by": "court_region",
    "filters": {"year_from": 2023, "year_to": 2025},
})
```

`group_by`支持`year`、`court`、`court_region`、`accident_region`、`party_region`、`dispute_type`、`issue`、`outcome`。筛选支持三种地区、纠纷类型及年份起止。事故地、法院地、当事人所在地分别处理，不从法院名称推测所在地。

结果包含：

- `records`：每组数量、未知组、分母、占比、案件组与来源；分母是筛选后独立案件组数，包含未知分组。
- `samples`：入选案件、定位字段、逐案损失金额、原文卡片及范围提示。
- `excluded`：来源失败、字段缺失、多个版本未决、同组记录冲突或被筛选排除的原因。
- `duplicate_sources`：同案同内容多个来源的折叠记录。
- 样本量、有效/缺失值、筛选前规模、实际日期范围及日期缺失数量。

结果只描述选定样本，固定为`insufficient`或无样本时`empty`，不计算胜诉概率、不推断某地区更容易获支持。金额只按案件和损失项目展示，不跨案件/币种求总额或混合比较。

## 成员二的数据适配

注入`CaseService(..., source_resolver=resolver)`；读取器按SourcePointer返回同ID、同版本的`case`来源，含标题、定位、原文。统计额外读取以下**来源侧**标注，调用方/模型不能直接在统计请求里填写统计结论：

```python
{
    "source_id": "case-a", "version": "v1", "source_type": "case",
    "title": "合成案例，仅示例", "locator": "第1段",
    "text": "案号：合成案甲；裁判日期：2025-01-02；法院地区：广东；维修费裁判金额：3000.00元",
    "court": "合成法院", "decision_date": "2025-01-02",
    "case_group_id": "synthetic-group-a",  # 可选；关联审级/同案分组由资料侧标注
    "statistics": {
        "case_number": {"value": "合成案甲", "quote": "案号：合成案甲"},
        "decision_date": {"value": "2025-01-02", "quote": "裁判日期：2025-01-02"},
        "court_region": {"value": "广东", "quote": "法院地区：广东"},
    },
    "awards": [{"loss_type": "维修费", "amount": "3000.00", "currency": "CNY",
                "quote": "维修费裁判金额：3000.00元"}],
}
```

每个统计字段需要`value`和逐字`quote`；值必须出现在引文内，引文必须出现在该版本原文内。首版日期要求可逐字定位的ISO日期，不自动转换“二〇二五年”等表达；不满足时算缺失并显示原因。`未知/未载明`不算有效地区。

案号是计入前提；无案件组ID时按规范化案号去重。关联审级识别仍依赖成员二的分组标注；同组不同内容、同案号分到不同组或同来源多个版本未决时，暂不计入并要求选择，避免静默择一或重复计数。每次最多200个来源，按30个一组调用已有来源检查。

逐字定位只验证记录可以回查，不能证明“某处地名就是法院地区”或“某金额就是最终裁判金额”。字段角色、请求额与裁判额区分、币种、案件关联及可比性仍需成员2/5独立标注和复核；结果的`semantic_metadata_checked`固定false。

## 对接与验证

成员1按`records`绘图，展示范围、分母和未知组，并用`samples/source_checks`实现回查。成员2提供案例及上述标注。成员4决定何时筛选或追加检索。成员5验证去重、筛选、缺失、来源与解释，不能将本地合成测试当独立司法样本评测。

新增`test_case_statistics.py`共14项测试。实现：`statistics.py`及`visualization.py`。`build_case_statistics`作为新Python接口保存可追溯输出，不扩充原10个Agent工具清单；注册由成员4明确决定。
