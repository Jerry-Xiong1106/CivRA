# 功能2：材料读取、OCR与候选字段提取

更新日期：2026-11-09。成员三的独立业务实现；字段规则版本 `case-document-fields-0.2.0`。

本次沿用 `CaseService.parse_documents`、`ParseDocumentsRequest`、`ParseDocumentsData`，没有改变原十个工具的参数或注册。上传网页、权限校验、Agent追问及独立评测仍由对应成员接入。

## 已实现范围

1. UTF-8文本、Word正文、PDF文字层、扫描PDF和单帧图片读取；沿用可切换OCR与现有PaddleOCR CPU配置。
2. Word按照正文中段落和表格的原顺序展开，支持常见的“字段名—字段值”两列表格。
3. 明确标签后的10类候选字段，支持半角/全角数字、中文/英文冒号、制表符分列，以及同一页中紧邻的“标签行＋值行”。
4. 保留文件版本、提取版本、页码、精确文字区间及OCR值所在行的坐标；标准化不改写原文引用。内容相同的不同页保留不同候选ID与原始页码。
5. PDF逐页保留成功结果；空页、失败页、超限页和截止时间后的未处理页均返回提示。PDF栅格化前检查3000万像素上限，默认最多50页。
6. 所有提取事实都是 `document_record / unconfirmed`，解析不会保存或确认案件事实。调用方审核结果后，通过已有 `update_case` 保存候选。

## 字段覆盖

| 字段 | 可识别的明确标签 | 处理 |
|---|---|---|
| `accident_date` | 事故日期、事故发生日期 | 有效年月日转为YYYY-MM-DD |
| `accident_location` | 事故地点、事故发生地点 | 保留原文 |
| `claimant_name` | 原告、原告姓名、原告名称 | 保留原文；不从驾驶人等角色推断原告 |
| `defendant_name` | 被告、被告姓名、被告名称 | 保留原文；不推断诉讼地位 |
| `insurance` | 保险关系、保险情况 | 保留原文，不判断保险覆盖 |
| `liability_finding` | 责任认定、责任记载 | 保留原文，不推定责任比例 |
| `losses.<file_id>.repair_amount` | 维修金额、维修费用、维修费、修理费 | 人民币金额候选 |
| `losses.<file_id>.medical_amount` | 医疗金额、医疗费用、医疗费 | 人民币金额候选 |
| `losses.<file_id>.towing_amount` | 拖车费 | 人民币金额候选 |
| `losses.<file_id>.appraisal_amount` | 鉴定费 | 人民币金额候选 |

金额接受普通非负数字、正确的三位千分组、最多两位小数及可选人民币标识；标签金额未注明币种时按CNY候选处理，仍需用户确认。例如 `￥３，０００．５０元` 的值为 `3000.50`，原文引用保持 `３，０００．５０`。

`1,2元`、`12.345元`、`100-200元`、`100万元`、`100美元`、百分比、估算和日期范围不会被截取成确定值，返回提示并保留原文字段。未覆盖的自然语言、中文大写金额、跨页字段和复杂多列表格需要人工核对或后续模型接入。同一字段出现两个不同值时保留两个候选，不自行选定。

## 调用示例

```python
from backend.case import CaseService, SQLiteCaseRepository
from backend.case.ocr import OcrEngineRegistry

# 案件必须已经存在；应用先核验当前用户对case_id有访问权限。
repo = SQLiteCaseRepository("var/cases.sqlite3")
service = CaseService(repo, ocr=OcrEngineRegistry().create(config["engine"], config["options"]))

service.assets.put_file(
    case_id=case_id, file_id="invoice_01", file_version="1",
    file_name="invoice.png", media_type="image/png", document_type="other",
    content=image_bytes,
)
result = service.parse_documents({
    "context": {
        "request_id": "parse-invoice-01", "case_id": case_id,
        "experiment_run_id": None, "deadline_at": None,
    },
    "case_version": current_case_version,
    "file_ids": ["invoice_01"],
    "file_versions": {"invoice_01": "1"},
    "ocr_mode": "auto",
})

if result.data is not None:
    for fact in result.data.extracted_facts:
        ref = fact.material_refs[0]
        original = service.assets.read_extraction(case_id, ref)
        assert original == ref.quote
    # result.data.documents：文件/证据候选
    # result.data.text_segments：原文、页码、坐标、引擎、分数
    # result.data.extracted_facts：尚未确认的结构化字段
    # result.data.warnings：覆盖范围、失败页和待核对问题
```

`insufficient` 可以包含有效解析结果和候选事实；调用方不能将其全部丢弃，也不能把“有候选”解释为全部页面已经成功。没有任何文件成功解析时返回 `error`，详细原因位于 `error.details.warnings`。解析本身会保存文件提取文本，案件快照和案件版本不变。

## PDF手写批注与OCR策略

| `ocr_mode` | 行为 | 调用时机 |
|---|---|---|
| `auto` | 优先读取可用文字层；没有文字或文字层读取失败时尝试OCR | 普通材料；有文字层时不能保证覆盖手写批注 |
| `always` | PDF逐页渲染并请求包含可渲染批注后做OCR；图片做OCR | 已知有手写添加或文字层不完整时；需要核对原图及批注是否出现在渲染中 |
| `never` | PDF只读文字；缺失页明确提示；图片报错 | 仅允许原生文字读取时 |

Word和纯文本不支持 `always`；含嵌入扫描图的Word须先由上传方转换为PDF或单独图片。无法正常渲染的特殊批注仍需上传方提供可见的平面化PDF或图片，不保证OCR自动恢复它们。截止时间在文件/页边界检查，不会强制中断一个正在进行的OCR调用。

Word页眉页脚、浮动文字、嵌套表格、修订和原始分页未覆盖。OCR分数不是正确率；姓名、金额和手写内容仍须用户核对。规则只能识别列明的明确标签，没有完成通用材料理解。

## 对接清单

| 对接对象 | 成员三提供 | 对方需要实现/提供 | 验收 |
|---|---|---|---|
| 成员1 | 文件登记存储、Python解析、原文引用和坐标 | 上传接口、权限、文件版本、原图/文字对照页、重试和OCR模式入口 | 同一文件能回到对应版本和原文，失败页可见；PDF存在手写时可显式请求OCR |
| 成员4 | 未确认候选、逐页提示、已有更新入口 | 候选解释、用户确认语义、补问、是否重试 | 不自动确认字段，不把缺失页当作没有材料，使用真实用户回复 |
| 成员5 | 测试、演示、字段规则范围及错误样例 | 独立打印/真实手写样本、页级文字与字段标签 | 分开评估OCR文字识别和字段提取；不得把合成开发样例当作独立测试集 |

## 可复现实验

在仓库根目录运行：

```powershell
python -m unittest discover -s tests/case -p 'test_document*.py'
python scripts/case/demo_documents.py --output-dir var/documents-demo-text
python scripts/case/demo_documents.py --output-dir var/documents-demo-ocr --ocr-config data/config/member3_ocr.json
```

每次演示使用新目录，保存原始合成材料、SQLite演示库及 `result.json`；文本/Word无需OCR，传入OCR配置才运行真实引擎。已有本地模型可以通过配置的 `detection_model_dir` 和 `recognition_model_dir` 指定；本次沿用CPU small模型，无选型变更。示例脚本默认使用Windows微软雅黑字体，其他环境通过 `--font` 传入中文字体。

2026-11-09验证结果：

- 新增20项测试，材料相关测试共38项通过。
- 全量成员三回归收集166项，148项通过，18项因当前副本缺少成员四Runtime而跳过。
- 真实PaddleOCR CPU演示通过：合成文本、Word、打印图片均提取出预期10项；逐项原文回查一致，候选全部未确认，案件快照未变。
- 本机合成图片首次加载及处理约19.1秒，包含模型加载；单次结果不代表轻薄本速度或识别准确率。
- 真实手写、歪斜/模糊、多列材料及独立评测仍未完成。

相关代码：`backend/case/documents.py`、`backend/case/field_extraction.py`；测试：`tests/case/test_document_fields.py`；演示：`scripts/case/demo_documents.py`。
