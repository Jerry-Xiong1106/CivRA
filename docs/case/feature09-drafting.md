# 第9部分：起诉状草稿生成与导出

更新：2026-11-09。成员三案件业务层；本次未拉取远程更新。

## 已实现的行为

用户选择诉讼并确认事实、请求后，`CaseService.draft_document` 根据指定案件版本和模板版本生成填写草稿，同时支持 Markdown、可编辑 Word 和固定页面的 PDF。三类纠纷为交通事故、民间借贷、买卖合同。

- 采用仍有效、已确认、无未解决冲突且值已知的所选事实；冲突或未知事实进入 `withheld_fact_ids`，不会作为确定事实填入。
- 所选事实关联已排除材料时拒绝生成；同一字段不能同时选择两条事实。案件快照必须与本地保存的同版本内容一致。
- 只列 `user_confirmed` 请求。请求依赖未选入的事实或已排除材料时暂缓列入；缺乏事实/材料关联时标注待核对，不把用户意愿当成已证明的法律权利。
- 文稿包含当事人与送达信息、请求、事实、理由、材料目录、致送签署和补充清单。电子送达同意、无代理/无担保、法院、签署日期等不自动猜测。
- 材料目录从本次事实和请求的关联材料产生，并保留文件版本、事实编号和证明事项；不会据此宣称证据已获采信。
- 模型未接入或缺乏合格法律来源时，仍可导出事实填写草稿，理由保留待补充。
- 同一内容块渲染三格式；用户文本按纯文本处理，不执行 HTML/Markdown。PDF嵌入中文字体并检查缺字，A4页面、固定边距和页码；Word可编辑。两者分页不要求相同。
- 所有选定格式先在内存生成并校验，再在同一数据库事务保存文件和输出。任何格式失败、保存时版本变化或超时均不留本次部分文件。
- 相同 `request_id` 和完整请求返回原结果，不重复调模型/导出；同编号改变内容或格式会报错。案件改变后用新的编号重新生成，旧结果继续保留且可判断过期。

草稿始终为 `status="insufficient"`、`review_status="needs_review"`。此状态表示仍需用户及法律核验，不代表导出失败。业务失败为 `status="error"`，不要尝试读取 `data`。

## Python调用

```python
from backend.case import CaseService, SQLiteCaseRepository
from backend.case.templates import CURRENT_VERSION

repo = SQLiteCaseRepository("cases.db")
service = CaseService(repo)  # 真实模型/来源读取器由调用方注入
case = repo.get_case(case_id)  # 必须已真实选择 litigation，不能由起草工具代选
result = service.draft_document({
    "context": {
        "request_id": "draft-request-001", "case_id": case.case_id,
        "experiment_run_id": None, "deadline_at": None,
    },
    "case_version": case.case_version,
    "case_snapshot": case,
    "route": "litigation",
    "confirmed_fact_ids": selected_fact_ids,  # 用户选定的已确认事实ID
    "template_id": "cn_complaint_" + case.dispute_type,
    "template_version": CURRENT_VERSION,  # civra-fields-v2
    "source_refs": [],  # 有真实来源时传入固定ID和版本；空列表保留理由待补充
    "export_formats": ["md", "docx", "pdf"],
})
if result.status == "error":
    print(result.error.code, result.error.message, result.error.details)
else:
    output = result.data
    for fmt, artifact_id in output.structured_data["artifacts"].items():
        item = service.assets.get_artifact(case.case_id, artifact_id)
        # 前端后端完成用户权限验证后，以filename、media_type、content提供下载。
        print(fmt, item["filename"], item["media_type"], len(item["content"]))
```

`export_formats` 省略时保留旧默认 `["md"]`，使用三格式须显式传入。原有10个工具名称和请求模型保持不变，不新增模型自行执行导出的工具。新依赖加入 `requirements-case.txt`。

模板查询：`service.drafter.templates.list_templates(dispute_type)` 默认返回该类别最新注册的内置配置；`include_versions=True` 可查看历史。实际起草必须明确版本，不接受 `latest` 或自动回退。`civra-fields-v1` 配置继续可用；旧请求重放继续返回旧结果。

## 来源与模板校对记录

2026年原配置的三份法院附件本次均返回HTTP 403，因此没有宣称读到了它们。已读取[中华全国律师协会公开附件](https://www.acla.org.cn/upload/media/2024-04-25/8de31284e766dd100a19f1d88d462402.pdf)中三类起诉状的文字，并检查各类首张表页的版面。

| 类型 | PDF物理页（从1计） | 印刷页码 | 本次用途 |
| --- | --- | --- | --- |
| 民间借贷 | 3–6 | 6–9 | 当事人/送达、请求、借贷事实、担保等字段组 |
| 买卖合同 | 27–31 | 30–34 | 当事人/送达、买卖双方请求、履行、违约及担保字段组 |
| 交通事故 | 86–88 | 89–91 | 当事人/送达、损失请求、事故/责任/保险字段组 |

附件SHA256：`f9fc415b2d4e96567eecc4a8864a9407a67554cd2432d18cb2199790388a1ff5`。机器可读记录在 `data/templates/member3_complaint_source_review.json`。

`v2`是按上述来源整理的**字段组填写草稿**，未逐一拆分自然人/法人身份子项、每种担保子项、每个赔偿项目和勾选格，也未复刻原表版式。代理权限等保留在汇总字段内；复杂赔偿计算仍不在本次范围。调解意愿、拟提交法院和核对提示包含项目已有的辅助字段，不据此声称每张原表都有相同栏目。实际提交需核对受理法院当前表格和适用要求。

## 模型与来源接入约定

成员二提供 `source_resolver(SourcePointer) -> dict`：`source_id`、`version` 与请求完全相同，且有 `source_type`、`title`、`locator`、`text`，可带有效HTTP(S) `url` 和生效日期等元数据。沿用第4部分来源定位检查；读取失败、ID/版本不符、缺失标题/定位、类型错误都进入 `source_issues`。表格模板不能当作实体法律依据，`short_excerpt_only` 不送入理由生成；来源多个版本未决时不自动择一。元数据检查不等于法律适用已核验。

成员四注入 `MeteredLLMDraftingGateway(call_model)`，`call_model` 应绑定当前运行预算、超时、模型计量和轨迹。新增 `compose_document(facts, sources, context, claims, template)`，模型可获得已确认请求及纠纷类型；保留旧 `compose(facts, sources, context)` 的自定义适配兼容入口。所有网关返回均再次统一检查，不允许未知/重复事实或来源ID、空段落或超过长度上限的结果。

模型返回 `paragraphs=[{text, fact_ids, source_ids}]`，每段至少关联一个本次事实和一个来源。至多20段、每段6000字；JSON模型输入上限60000字符。自定义网关需要自行控制调用预算。引用编号合法不能证明段落内容真实或法律论证成立，必须交第10部分独立核验。

`reasoning_state`：

| 值 | 含义 |
| --- | --- |
| not_configured | 未配置起草模型 |
| insufficient_sources | 已配置但无合格来源/事实，或模型返回空段落 |
| generation_failed | 模型失败或返回结构/引用非法；理由留空 |
| generated_needs_verification | 生成了通过结构检查的段落，语义仍待核验 |

网关超时直接返回 `timeout` 且不保存本次结果。其他生成失败降级为填写草稿。真实模型预算、失败调用用量仍应由成员四的运行追踪器记录，不能只统计成功草稿里的 `model_usage`。

## 输出与存储接入

`structured_data`主要字段：`fields`、`field_rows`、`claims`、`facts`、`withheld_fact_ids`、`material_directory`、`findings`、`content_blocks`、`source_cards`、`source_issues`、模板来源与审查元数据、`reasoning_state`、`model_usage`、`artifacts`、`export_manifest`。清单每种格式含文件ID、文件名、MIME、字节数和SHA256；`artifact_id`仅为首个请求格式，下载多格式须遍历`artifacts`。

成员一替换本地资产存储时，须实现 `artifact_identity(case_id, filename, content)` 和 **`save_output_bundle(output, artifacts, deadline_at=...)`**，保证版本检查、全部文件及输出的事务一致性。不要拆成逐文件持久化。单文件上限20MiB、每次1–3格式、正文上限1000块/200000字符。本地SQLite参考实现已完成；权限与跨用户隔离仍在成员一入口验证。

PDF运行依赖ReportLab和可嵌入的中文TrueType字体。Windows优先系统宋体，其次黑体；Linux可配置兼容的TTF/TTC，或安装代码候选列表中的文泉驿/文鼎字体。使用`CIVRA_PDF_FONT_PATH`指定字体；部署者确认字体授权。缺字体/缺字会返回明确配置错误，本次全部导出不保存；也可用新请求仅导出MD/Word。运行不依赖Word或LibreOffice。

## 对接清单

| 成员 | 需要对方接入 | 本模块提供及验收重点 |
| --- | --- | --- |
| 1 | 模板选择、事实/请求确认、预览下载、授权存储 | 三类模板、缺失项、内容块、三格式文件清单；确认下载内容与版本一致，显示草稿状态 |
| 2 | 精确版本法律原文、当前法院原表及适用信息 | 来源读取与定位核对；不把2024参考稿说成2026法院原表 |
| 4 | 真实统一模型、预算与轨迹、失败/追问/核验循环 | 已确认事实和请求、模型网关、引用链接、缺失清单；模拟调用成功不能替代真实联调 |
| 5 | 独立文书质量和导出评测 | Python入口、三类合成样例、格式与失败状态；独立检查事实忠实性/引用支持/材料遗漏 |

## 验证与演示

```powershell
python -m pip install -r requirements-case.txt
python -m unittest discover -s tests/case -p "test_drafting_exports.py"
python scripts/case/demo_drafting.py --output output/drafting-demo
```

演示目录必须为空或不存在，生成三类合成案件的三种文件、结构化JSON、清单和隔离演示数据库，不接触真实案件。合成陈述不是票据或真实法律证据。

2026-11-09记录：新增16项专项测试通过；全量322项中304通过、18因缺少共享Runtime跳过；原有40个交接场景通过。版式调整后专项测试复核通过。三份PDF共9页、三份Word实际渲染共10页，均逐页检查中文、边界和页码。标准DOCX渲染器因本机没有LibreOffice而不可用，改用独立后台Word实例只读转换检查；这只是开发检查工具，不是业务导出的运行依赖。自动化测试和样稿不构成法院可受理性或法律语义质量评测。
