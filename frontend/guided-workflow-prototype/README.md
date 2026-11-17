# CivRA 第二版固定流程原型 · 两处参考案例补充

基于 `develop@922493e`。分支：`codex/guided-workflow-case-visuals`。

按最新确认范围，恢复第二版整体布局，仅在以下两个页面补充参考案例：

1. **结果分析**：原来的三张分析卡片保留，在下方增加参考案例，查看样例理由与本案需要核对的差异。仍需先确认案情。
2. **案情图谱**：原来的程序状态和时间线保留，在下方增加默认折叠的“参考案例与样本统计”；支持地区、法院、年份筛选和样本结果分布。

法律依据、只咨询法律、案情沟通、维权路径、证据材料及起诉状页面沿用第二版；没有案例子标签、收藏系统或关系编辑器。案件列表、七标签顺序、五步流程和进入限制保持不变。

## 启动

在本目录运行：

```sh
python -m http.server 8782 --bind 127.0.0.1
```

浏览器打开 `http://localhost:8782/`。Windows 可运行 `启动预览.ps1`。无需 npm 依赖，按 Ctrl+C 停止服务。

## 验证

```sh
node --check app.js
node --test policy.test.cjs reference.test.cjs
```

共 15 项测试：12 项原有流程规则，3 项样本过滤、统计分母与模拟数据标识。浏览器验证见 `验证记录.md`。

## 数据与对接边界

- Python 后端没有修改；此原型尚未连接真实 Agent、RAG 或 OCR。
- 浏览器本地数据键继续使用 `civra.guided-workflow.prototype.v2`，保留此前案件记录。
- 案例全部是明确标注的虚构样例：交通事故 24 条、借贷 6 条、买卖合同 6 条。没有真实案号或原文链接，不能作为司法引用或胜诉率。
- 结果分析上方仍是第二版结构示意，不是对本案的实际判断。
- 法律查询恢复第二版的调解时点来源示例；其他问题显示待接入 RAG。
- 文件仅登记名称和大小；文书仅演示 Markdown，官方原表及 Word/PDF 待对接后端。
- 前端步骤限制仅用于交互评审，正式业务必须由服务器校验。

`styles.css` 为原有第二版样式；`references.css` 的规则全部限制在新增参考案例区内。`reference-data.js` 和 `reference-logic.js` 只服务于上述两处补充。

详细后端设计见 `设计说明.md`；各成员对接清单见仓库 `docs/case/CivRA_固定流程与可视化改造_成员对接清单.md`。

预览截图：`preview-outcomes-v2-restored.jpg`、`preview-visuals-v2-restored.jpg`。
