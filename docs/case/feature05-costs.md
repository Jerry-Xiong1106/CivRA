# 功能5：材料金额分类与普通财产案件受理费估算

更新日期：2026-11-09。用户已确认加入普通财产案件受理费，特殊减免等另行处理。

## 材料金额明细

新增 `material_amounts_v2`，支持维修费、医疗费、拖车费、鉴定费四类已确认材料金额。仅汇总调用方明确选定的事实，不自动把所有金额、诉求总额、律师费、诉讼费或模型费用相加。

`CaseService.summarize_material_costs`返回逐项明细：

| `rows[].state` | 含义 |
|---|---|
| `included` | 本次可计入的已确认金额及来源位置 |
| `duplicate` | 相同文件字节、同类金额、同一原文位置的重复候选，计一次 |
| `excluded` | 来源材料已经明确排除，不计入但继续展示 |
| `superseded` | 旧事实已被明确取代，不计入 |
| `unresolved` | 未确认、字段冲突、来源/位置不足、疑似重复等，须先处理 |

同一PDF内不同原文位置保留不同记录，不仅凭文件哈希就合并。同一原文件的同类金额有多个位置时，目前先暂停汇总，提示明确独立票据项目或拆分材料，不自动判断它们是重复页还是不同票据。相同字段还有不同金额时，不能仅选择其中一个确认值绕过冲突。文字疑似重复沿用第三部分的有限范围检测，发现候选时先等用户确认，不自动扣减。

`known_category_subtotals`只表示已纳入行的分类小计；**存在未解决项时`total=null`**。多币种分别显示小计，不兑换或输出跨币种总额。没有可计入金额时保留未知，不填零。

```python
request = {
    "context": context,
    "case_version": current_case_version,
    "calculation_type": "materials_breakdown",
    "rule_version": "material_amounts_v2",
    "inputs": {"fact_ids": selected_money_fact_ids},
}
detail = service.summarize_material_costs(request)  # 新增独立Python入口，含逐项原因和来源
calculation = service.calculate_costs(request)     # 沿用原工具，保持CalculationData结构
```

原工具在条件齐备时，`amounts`包含`total`以及`repair_amount.CNY`等分类键；分类小计是总额的组成部分，**不能再与total相加**。未解决时原工具不输出金额字典。`calculation_type="materials_total"`搭配新版本时只输出总额。

旧`arithmetic_v1`仍用于历史调用与重算，保留原求和、乘数和按文件哈希/项目汇总的行为；它没有v2逐位置与疑似重复检查，新开发应明确选v2。旧数据没有提取版本时只能按已给定来源定位处理，不能声称完成OCR原文回查；限制会保留。手续费、利息、责任分摊、保险抵扣等不自动推断。

## 普通财产案件受理费

新增`property_case_acceptance_fee / cn_property_acceptance_fee_2007_v1`，采用第十三条第一项的分段累计数据。已核对的主要来源为[最高法院公布的《诉讼费用交纳办法》](https://www.court.gov.cn/zixun/xiangqing/5092.html)，边界测试另参照[最高法院诉讼费须知的速算表](https://www.court.gov.cn/zixun/xiangqing/78662.html)。

| 请求金额区间（元） | 本段处理 |
|---|---|
| 不超过10,000 | 基础50元 |
| 10,000—100,000 | 超出前段部分×2.5% |
| 100,000—200,000 | 本段×2% |
| 200,000—500,000 | 本段×1.5% |
| 500,000—1,000,000 | 本段×1% |
| 1,000,000—2,000,000 | 本段×0.9% |
| 2,000,000—5,000,000 | 本段×0.8% |
| 5,000,000—10,000,000 | 本段×0.7% |
| 10,000,000—20,000,000 | 本段×0.6% |
| 超过20,000,000 | 超出部分×0.5% |

配置存放于`data/config/member3_property_fee_rule.json`，记录规则版本、来源、短引文、分段数值和核对日期。没有把完整网页或完整法条缓存进仓库；数值数据可与链接逐项核对。

```python
fee = service.calculate_costs({
    "context": context,
    "case_version": current_case_version,
    "calculation_type": "property_case_acceptance_fee",
    "rule_version": "cn_property_acceptance_fee_2007_v1",
    "inputs": {
        "claim_value": {"amount": "100000.00", "currency": "CNY"},
        "case_category": "ordinary_property",
        "procedure": "first_instance_ordinary",
        "adjustment": "none",
        "filing_date": "2026-11-09",
    },
})
```

输入须明确为中国内地普通财产案件、一审普通程序及不作特殊调整，金额为正数人民币、最多两位小数。**请求金额须明确提供，程序不自动从材料总额推定。**未知或零金额、外币、特殊程序和未支持的调整参数会明确拒绝。

输出分别提供：

- `fee_exact`：Decimal分段累计原值。
- `fee_estimate`：按`ROUND_HALF_UP`展示到两位小数；这是工程展示约定，最终按法院缴费通知核对。
- `formula`、输入、逐段基数/比例/小计、适用假设及准确规则来源。

两个fee字段是同一笔费用的两种表示，不可相加；受理费也不并入材料损失总额。示例100,000元得到2,300元基础估算。

适用日期使用明确的`filing_date`，与事故日期分开。当前规则生效起点按原文记录为2007-04-01，本地核对截至2026-11-09；超出该范围返回`insufficient`且不出金额，待重新核实并选择规则版本。核对日期不是法律失效日期。

本轮不处理减免、减半、反诉、上诉、再审、非财产案件、保全/执行费用、退补费或最终承担人；这些不在用户确认的首版估算范围内。不接受`latest`替代明确版本，也不允许调用方用不同来源指针冒充内置规则。

## 来源与核验接入

费用来源采用明确的`member3.local_rule.*`命名空间，读取器为`CaseService.get_source`。它同时保留成员二外部来源解析回调。本地读取器只返回短引文、官方定位与元数据，不冒充完整条文或成员二知识库。

Agent适配时，应将相同读取器传给已有注册入口，例如：

```python
register_case_tools(registry, service, source_resolver=service.get_source)
```

本次没有修改成员四注册实现。原`calculate_costs`协议未变，新类型与版本仍通过原入口调用；逐项明细为新增独立Python入口。已有核验器可按相同规则重算材料与费用结果，语义/法律核验未接入时仍明确列为未执行。

## 对接与验证

| 成员 | 需要对接 |
|---|---|
| 成员1 | 金额明细、计入/不计入原因、原文位置；损失金额与受理费分区展示 |
| 成员2 | 正式完整条文、适用元数据；后续新增法律计算规则的来源与版本 |
| 成员4 | 取得明确计算输入、处理疑似重复/冲突、选择规则版本；接入组合来源读取器 |
| 成员5 | 独立金额期望、分类与重复边界样例、法律适用复核；区分算术通过和适用正确 |

新增费用/明细测试21项，覆盖全部费率边界、边界前后1分、精确值与取整、特殊情形拒绝、来源版本、可重算性及金额明细。与第四部分19项合计新增40项。全量236项中218项通过、18项因本地副本缺少成员四Runtime跳过；原40个工具交接场景仍通过。

```powershell
python -m unittest discover -s tests/case -p 'test_cost_details.py'
python scripts/case/demo_research_costs.py --output-dir var/research-costs-demo
```

联合演示使用合成案件、明确模拟的用户确认及合成比较案例：材料3,000元与受理费50元分开，来源可定位、计算可重算、案件快照不变。查询计划没有执行真实检索；合成案例不是法律依据。真实检索、真实模型、界面与独立法律评测仍待联调。
