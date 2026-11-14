# 公开合成开发样例

本目录所有姓名、材料、票据和案情均为合成内容，供成员三结构开发及成员四/五联调使用。

| 文件 | 用途 |
|---|---|
| `case_snapshot.json` | 合法业务结构，保留日期冲突、重复材料和缺失保险信息 |
| `check_evidence_request.json` | 下一批证据工具的合法请求样例，目前只实现请求校验 |
| `invalid_case_snapshot.json` | 非法证据引用，应被结构校验拒绝 |
| `example_output.json` | 人工编写的业务输出结构示例，非自动分析结果 |
| `expected_review.json` | 人工开发预期，不进入 Agent 上下文，不用于最终测试 |
| `materials/` | 原始合成 UTF-8 文本，哈希及字符位置与快照一致 |

`repair_invoice.txt` 与 `repair_invoice_copy.txt` 字节相同。日期冲突和重复票据目前由样例作者标出；下一批的业务工具应从输入检测，不能读取 `expected_review.json` 返回答案。

从仓库根目录执行 `python scripts/case/build_dev_fixture.py` 可重复生成本目录的 JSON 和材料。
