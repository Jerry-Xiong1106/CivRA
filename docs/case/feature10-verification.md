# 第10部分：结果核验与修正

更新：2026-11-09；实现版本`case-tools-0.5.0`。本轮完成成员三的核验加固、修正任务准备和两次核验比较，真实模型与执行循环仍由成员四接入。

## 行为与边界

`verify_output`保留原工具入口，核对案件版本、有效确认事实、证据排除状态、缺失字段、复制的事实和请求、计算重放、文书字段、导出文件哈希与内容块、来源身份/版本及保存的引文位置。

确认事实仍有未解决冲突或未知值时，不作为可靠输出依据。结构化事实和诉求格式错误会返回具体问题，不导致整次检查崩溃。所有自定义语义网关都经过统一返回检查，拒绝未知ID、重复问题ID、非法类别、伪造来源/材料坐标和异常用量；网关收到的是副本，不能修改待核验输出或案件。网关超时不保存本次报告。

来源读取沿用第4部分：固定ID和版本、正文、标题、定位必须可核对。已定位的短摘录不会被误判为“错误引用”，但完整原文与适用性仍列为未完成检查。元数据合法、引文存在不代表法律结论正确。

`VerificationData`新增：

| 字段 | 用途 |
| --- | --- |
| target_output_id / target_output_version / target_digest | 绑定本次实际检查的输出及内容 |
| source_checks | 来源定位、原文哈希、范围提示；语义支持不由结构检查认定 |
| correction_tasks | 按问题准备补证、补字段、重算、重新检索、重生成或继续核验任务 |
| disposition | needs_correction / needs_review / checks_passed |
| checks_performed / checks_not_performed | 保留原字段，区分已执行和未完成的检查 |

`checks_passed`只代表本次已接入检查未报告问题，不是法律正确性或法院受理保证。核验不会自动把原成果改成`reviewed`。

## Python接口

```python
case = service.repository.get_case(case_id)
context = {"request_id": "review-001", "case_id": case_id,
           "experiment_run_id": None, "deadline_at": None}
target = service.assets.get_output(case_id, output_id)["output"]
review = service.verify_output({
    "context": context, "case_version": case.case_version, "case_snapshot": case,
    "candidate_output": target, "cited_sources": target.source_refs,
})
if review.status != "error":
    plan = service.prepare_output_correction({
        "context": {**context, "request_id": "repair-plan-001"},
        "case_version": case.case_version,
        "verification_output_id": review.output_refs[0],
        "verification_output_version": 1,
    })
```

`prepare_output_correction`只接受已保存的核验报告，读取其精确目标版本并核对内容哈希。报告与目标不匹配、目标不存在、报告已落后于当前案件时拒绝；应先重新核验。旧版本核验报告缺少目标绑定字段时，也需使用新请求编号重新核验。

完成修正后，以新请求生成新成果/新版本并再次调用`verify_output`。保留旧稿，不能直接改写已有输出版本。随后调用：

```python
comparison = service.compare_verification_reports({
    "context": {**context, "request_id": "compare-reviews-001"},
    "case_version": case.case_version,
    "verification_output_id": new_review_id,
    "previous_verification_output_id": old_review_id,
})
```

比较结果列出`still_reported`、`no_longer_reported`、`newly_reported`和检查范围是否缩减；`no_longer_reported`不能当作语义问题已解决，`semantic_resolution_proven`保持false。

本模块返回项目建议上限`recommended_max_rounds=3`，**不自行执行循环、不自行消耗模型预算、不保证Runtime已执行三轮上限**。真实轮次、预算、停止、追问和剩余不确定项由成员四保存及控制。

## 对接与验证

| 对接成员 | 对方提供 | 本部分提供 |
| --- | --- | --- |
| 1 | 问题展示、原材料跳转、人工确认入口 | 问题位置、修正清单及原输出版本 |
| 2 | 完整精确版本原文、适用时点和追加检索 | 来源读取检查及不可定位原因 |
| 4 | 真实语义模型、检索/重算/改稿循环、预算与三轮退出 | 网关、修正任务、已保存报告比较；业务函数不会自主运行Agent |
| 5 | 独立事实忠实性、引用支持与不利材料标注 | 规则/语义执行范围、合成错误与修正演示 |

新增`test_verification_corrections.py`共12项测试。入口为`verification.py`、`verification_support.py`、`corrections.py`及CaseService包装。原10个工具名称不变；两项新接口为Python应用接口，未自动注册为Agent工具。
