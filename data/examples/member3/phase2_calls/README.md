# 第二批实际调用样例

本目录包含合成案件的真实 `check_evidence`、`update_case` 和 `build_evidence_directory` 请求/结果，以及三个非法输入的结果。

重建：`python scripts/case/export_phase2_examples.py`。样例是公开开发数据，不能作为独立最终测试成绩。

第一次检查会发现四项待办：责任信息和保险缺失、日期冲突、重复票据。事故材料内的责任文字尚未被字段提取器转成 Fact；自动提取属于第三批，因此当前需要用户补充或确认。
