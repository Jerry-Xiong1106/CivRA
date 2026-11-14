# 成员三第三批实现与交接

本批增加材料解析、可替换 OCR、明确公式计算、路线比较与待办、文书填写草稿、规则核验和语义模型接入口，以及前端展示数据。接口均为同步 Python 调用。实现版本为 `case-tools-0.3.0`；前两批字段保持兼容，新增字段有默认值。

## 当前可运行范围

| 入口 | 行为 | 需要的配置或限制 |
|---|---|---|
| parse_documents | UTF-8 文本、Word 段落和表格、PDF 文字层提取；扫描页和图片 OCR；返回定位及候选事实 | OCR 依赖单独安装；首批正则字段为事故日期、维修金额、医疗金额和责任文字，其余文字仍完整返回 |
| calculate_costs | Decimal 金额求和、明确乘数计算、已确认材料金额去重汇总 | 不推断责任比例、利息规则或赔偿标准；财产案件受理费待用户选型后接入 |
| compare_routes | 并列比较协商、调解、诉讼的条件、步骤和未知项 | 是待核实的通用规划草稿，未实现地方机构和期限的法律检索适配 |
| get_route_workflow | 用户选择路线后生成步骤、联系对象、材料、完成条件和未解决时的选择 | 不替用户切换路线；具体入口、期限未核实时保留未知 |
| draft_document | 选择版本化模板，仅使用选定的已确认事实，生成 Markdown 填写草稿并存储 | 首批交通事故、民间借贷、买卖合同三个字段配置；原表尚未下载和逐项校对；Word/PDF 导出待用户选择 |
| verify_output | 检查版本、字段、事实/证据链接、结构化事实与诉求、来源版本及显式计算重算 | 真实语义核验需成员四传入模型网关；未执行的检查单独列出 |
| build_visualization | 当前案件时间线或证据关系图的结构化数据 | 前端由成员一绘制；保留未确认和已取代状态，不生成虚构统计 |

第二批的 update_case、check_evidence、build_evidence_directory 保持可调用；Runtime 适配现可登记十个业务工具。

## 本机 CPU OCR 配置

用户确定使用轻薄本 CPU 推理。首版选择 PP-OCRv6 small，4 个 CPU 线程，关闭额外方向分类和文档矫正。默认关闭 MKL-DNN：当前 Windows + PaddlePaddle 3.3.1 测试中，开启它会出现 `ConvertPirAttribute2RuntimeAttribute` 不支持的错误，关闭后真实推理成功；其他机器可显式开启并重新验证。

配置文件：`data/config/member3_ocr.json`。固定包版本：PaddleOCR 3.7.0、PaddlePaddle 3.3.1。模型名称显式指定为 PP-OCRv6_small_det 和 PP-OCRv6_small_rec，防止升级时静默改用更大的默认模型。OCR 模块导入和 CaseService 初始化不触发模型下载，首次识别时才加载模型。

```powershell
python -m venv .venv-case
.\.venv-case\Scripts\python.exe -m pip install -r requirements-case-dev.txt -r requirements-ocr.txt
$env:PADDLE_PDX_CACHE_HOME = "$PWD\var\ocr-models"
$env:HF_HOME = "$PWD\var\hf-cache"
.\.venv-case\Scripts\python.exe scripts/case/smoke_ocr.py --output-dir var/ocr-smoke-new
```

首次运行需要下载模型并预留依赖空间。图片应尽量清晰、端正；PDF 有文字层时默认直接提取，手写批注不会因此自动被识别。需要识别批注时对 PDF 显式传 `ocr_mode="always"`。Word 的 always 模式要求先转换成 PDF，当前不会自动渲染 Word。

2026-11-04 已在本机 CPU 对生成的简体中文和数字打印图片执行真实 PaddleOCR，识别出事故日期和 3000.00 元；同时跑通从实际 OCR 到业务层的演示。这是一张合成打印样例的运行验证，不能用来报告手写正确率、整体准确率或所有轻薄本的耗时。

官方说明：[OCR Python 接口与模型](https://www.paddleocr.ai/main/en/version3.x/pipeline_usage/OCR.html)。切换引擎无需修改解析器：实现 `recognize(bytes) -> OcrPage`，在 OcrEngineRegistry 注册 factory，再把创建的引擎传给 CaseService。

```python
import json
from pathlib import Path
from backend.case import CaseService, SQLiteCaseRepository
from backend.case.ocr import OcrEngineRegistry

config = json.loads(Path("data/config/member3_ocr.json").read_text(encoding="utf-8"))
engine = OcrEngineRegistry().create(config["engine"], config["options"])
repo = SQLiteCaseRepository("var/cases.sqlite3")
service = CaseService(repo, ocr=engine)
```

识别返回 engine、包版本、模型配置、每行文字、分数、四点框和警告。分数不是正确率；低于工程阈值 0.85 的行会提示核对。所有 OCR 候选事实均为 unconfirmed，不自动写回案件，也不会替用户确认。

## 上传、解析和回查

应用先验证案件访问权限，再保存文件。SQLiteCaseRepository 与 CaseAssetStore 是本地参考实现；文件原始字节、提取文字、产物和输出存放在同一数据库，成员一可通过注入 assets 替换应用存储。上传哈希由实际字节计算，不信任客户端报送的哈希。

```python
from backend.case.contracts import InvocationContext, ParseDocumentsRequest

record = service.assets.put_file(
    case_id=case_id, file_id="upload-001", file_version="1", file_name="材料.png",
    media_type="image/png", document_type="other", content=uploaded_bytes,
)
context = InvocationContext(request_id="parse-001", case_id=case_id,
                            experiment_run_id=None, deadline_at=None)
result = service.parse_documents(ParseDocumentsRequest(
    context=context, case_version=current_version, file_ids=[record.file_id],
    file_versions={record.file_id: record.file_version}, ocr_mode="auto",
))
if result.data:
    segment = result.data.text_segments[0]
    exact_text = service.assets.read_extraction(case_id, segment.material_ref)
```

文件版本不可覆盖；新版本使用新的 file_version。同一 file_id 存在多个版本时应显式传 file_versions，未明确时不会猜测“最新”。有唯一案件证据版本时可按该版本读取。每个文件默认上限 20 MiB，PDF 最多 50 页，单图最多 3000 万像素；多帧图片需转为 PDF 或单独图片。Word 限制解压后大小。

MaterialRef 新增 extraction_version 和 polygon。文字区间对应保存的页级提取文字，而非 PDF 二进制字节；OCR 框是传给引擎的图片像素坐标。提取版本包含引擎、模型配置、文字及识别几何结果，原文回查同时校验文件版本、提取版本、区间与引文。

解析返回 documents 和 extracted_facts；需要入库时把两组候选一起交给 update_case，保存后仍是未确认记录。用户确认由应用保存真实消息，再用既有确认机制更新。不要把解析成功理解为事实成立。

## 计算与版本化输出

金额必须为十进制字符串和明确币种。支持：

- sum_amounts / arithmetic_v1：`inputs={"amounts":[{"amount":"0.10","currency":"CNY"}, ...]}`。
- multiply / arithmetic_v1：`inputs={"amount":{"amount":"3000.00","currency":"CNY"},"multiplier":"0.3"}`。
- materials_total / arithmetic_v1：`inputs={"fact_ids":["已确认金额事实ID", ...]}`。

材料汇总只接受当前有效且已确认的金额，要求可关联一个已识别的原始文件；按文件哈希与金额项目去重。同材料出现不同金额、已排除来源、未确认值或条件不全时不输出总额。不同来源上的相同日期和金额不自动视为重复票据；重新拍摄同一票据的近似重复尚未覆盖。

自定义版本化规则由 `service.calculator.register_rule(name, version, handler)` 注入。此扩展入口不代表已验证任何法定收费规则。显式计算记录放入输出 structured_data.calculations 时，核验器会按同一输入和规则重算。

路线、目录、待办、草稿、核验和展示结果保存为 BusinessOutput；返回 output_refs。`assets.get_output(case_id, output_id, output_version=None)` 返回 output 与 stale；`get_artifact(case_id, artifact_id)` 返回文件名、类型和字节。文件下载仍需应用鉴权，不公开任意磁盘路径。

保存时在事务中再次检查案件版本和截止时间。输出不可原地覆盖，新版本必须连续递增。文书和语义核验相同请求重试会先读取已有结果，避免顺序重试重复调用模型；并发相同模型请求尚未实现跨进程排队合并，应用仍应按 request_id 控制并发。模型网关必须自身执行超时与预算控制。

## 三类文书与官方来源

首批选择由用户确认。模板 ID 为 cn_complaint_traffic_accident、cn_complaint_private_lending、cn_complaint_sales_contract；本地字段配置版本为 civra-fields-v1。可用 `service.drafter.templates.list_templates(dispute_type)` 查询。案件类型分别为 traffic_accident、private_lending、sales_contract，类型不匹配时拒绝套用；新增案由可注册新 DocumentTemplate。

已核实最高法推广的示范文本覆盖 67 类纠纷，三类只是首批实现范围。[最高法发布说明](https://www.court.gov.cn/zixun/xiangqing/468671.html)；[梁溪法院公开模板目录及三类附件链接](https://lx.wxfy.gov.cn/article/detail/2026/01/id/9147338.shtml)。2025 年的通知明确修订了旧试行文本，因此不能不经核实就把旧版原表作为现行原表。[通知原文](https://zmdzy.hncourt.gov.cn/upload/file/20250710/20250710153540_20511.pdf)

当前网络对上述 Word 附件下载返回 TLS/403 错误。代码已记录原表入口和目录日期，但没有缓存原表或编造文件哈希；本地字段配置只输出填写草稿，不声称完整复刻原表。origin_kind=official_reference_pending_form_review，成品中保留该限制。正式原表字段、格式与实例分离尚需取得附件后逐项核对。Word/PDF 导出也需用户选型，当前只登记 Markdown exporter；请求未配置格式会返回错误，不静默降级。

## 与成员二和成员四的模型对接

source_resolver(pointer) 返回同 ID、同版本的来源及全文。实质理由只接受标记为 statute（兼容 law、regulation）或 judicial_interpretation 的原文，模板目录不能冒充法律依据。

```python
from backend.case.drafting import MeteredLLMDraftingGateway
from backend.case.verification import MeteredLLMReviewGateway

# metered_call 必须绑定到当前 Runtime 运行的预算、模型、超时和轨迹。
# 不应直接拿一个未计量的模型客户端冒充此回调。
service = CaseService(
    repo, source_resolver=resolve_exact_source,
    drafting_gateway=MeteredLLMDraftingGateway(metered_call),
    semantic_gateway=MeteredLLMReviewGateway(metered_call),
)
```

回调使用 messages、max_tokens、temperature；返回成员四风格的 content、model、usage、cost、provider_request_id。网关不选择供应商或私自读取 Key。生成理由逐段校验 fact_ids、source_ids；未知 ID、错误 JSON 和意外工具调用被拒绝，草稿始终需独立核验。真实 LLM 尚未运行，离线测试替身只验证接口，不代表模型质量。

verify_output 不等于全文法律正确性证明。规则可发现结构问题；事实忠实性、引文是否支持结论、是否遗漏不利材料需要语义网关。未配置时列入 checks_not_performed，返回 insufficient；不能将其在评测中记为完整核验通过。网关模型用量随 model_usage 返回，成员四仍负责运行级计量和预算扣减。

## 成员对接与运行

| 成员 | 本批可交付 | 待接入 |
|---|---|---|
| 成员一 | 文件登记、版本化输出与文件取回、时间线及证据关系数据 | 上传与下载鉴权、真实界面、统一应用存储 |
| 成员二 | SourcePointer、精确来源解析回调、版本化模板登记 | 可追溯法律原文、地方法院程序信息、原表附件及字段核对 |
| 成员四 | 十个 ToolSpec、Python service、模型生成/复核网关 | 真实模型计量调用、工具并发与预算、用户确认语义 |
| 成员五 | 十组可重建请求/结果示例、动态 JSON Schema、错误与未执行检查 | 四种实验模式适配、独立标注与真实手写/文书样本评测 |

```powershell
python scripts/case/demo_phase3.py --output-dir var/phase3-demo-new
python scripts/case/demo_phase3.py --output-dir var/phase3-demo-ocr-new --ocr-image var/ocr-smoke-new/input.png
python -m pytest tests/case -q
```

演示目录必须为新目录，防止覆盖已有案件数据库。输出包括 cases.sqlite3、summary.json、十组 calls/*.json 和填写草稿 Markdown。演示固定用户回复明确标为合成测试数据，不是通用自然语言确认器。案件、材料、预期值都不作为最终评测金标准。

工具注册沿用 `register_case_tools(registry, service, source_resolver=...)`，不与同名 Mock 同时登记。ToolResult.data 保留完整 BusinessResult，实际数据仍在 data["data"]。请求不允许模型提供案件快照或伪造用户消息。新增工具的案号、版本和截止时间来自可信 ToolContext；OCR 定位同步转成 user_material 来源供运行轨迹展示。

本批仅使用此前保存的成员四 Runtime 104ca8a 副本联调，没有读取远程新更新。单独成员三分支缺少 Runtime 时会明确跳过联调测试，不能把这些跳过计作通过。

2026-11-04 完整集成副本最终验证：143 项测试通过，无跳过，另有 33 个参数化子检查通过。24 份动态 Schema 的结构通过检查，真实演示的十组请求/结果共 20 份 JSON 通过对应 Schema。隔离 CPU OCR 环境依赖检查通过。十组公开合成示例和实际打印图像识别记录位于 data/examples/member3/phase3_calls；使用的测试模型替身均明确标记，不计作真实 LLM 验证。

## 仍待用户确定和真实验证

财产案件受理费是否纳入首版，以及 Markdown、Word、PDF 导出范围仍待用户选择。原始法院附件逐项校对、真实 LLM 对接、真实手写识别评测和独立法律正确性评测尚未完成。当前通用路线模板不是根据案件检索法律后形成的个性化法律意见。
