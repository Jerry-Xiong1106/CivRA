# 功能3：证据整理、冲突检查与补证提醒

更新日期：2026-11-09。成员三的业务实现，首期仍按讨论稿覆盖交通事故中的维修费、医疗费。

## 本次规则决定与实现

用户已选择：**医疗票据＋诊疗记录，缺少时提醒补充或解释**。这是本项目的开发提示规则，不作为法院强制材料清单，不据此判定真实性、因果关系或法律证明力。

规则配置：`data/config/member3_evidence_rules.json`。

- 规则集版本：`case-evidence-rules-0.2.0`。
- 交通事故配置：`traffic_accident_v2`；未配置纠纷保留 `generic_v1`。
- 旧阶段导出的调用结果仍可能记录`traffic_accident_v1`；它们是历史快照，当前执行结果以本版本为准。
- 必要事实提示：事故日期、责任认定信息、保险关系。明确说“不知道/待核对”等仍保留为缺口；明确“未投保”不会被改为未知，但仍按事实确认流程处理。
- 金额、日期与身份等结构化字段继续检查不同记载；不能仅确认其中一个候选就自动抹掉其他值。
- 每张票据的金额字段保留文件范围，两个不同文件金额不同不直接算冲突，也不因金额相同就视为重复。

| `claim.kind` | 补证提示组 | 可满足该组的 `document_type` |
|---|---|---|
| `repair_cost` | 维修费用票据 | `repair_invoice` |
| `medical_cost` | 医疗费用票据 | `medical_invoice` |
| `medical_cost` | 诊疗记录 | `medical_record`、`outpatient_record`、`inpatient_record`、`diagnosis_certificate`、`discharge_summary` 任一种 |

医疗费的两组分别检查；同组中的任一种登记类型只表示该开发提示已找到对应类型，实际内容及本案关联仍需核对。撤回的诉求不再触发补证，已排除的材料不能满足提示。

如果案件中已有相应类型材料，但未关联该诉求，返回现有 `evidence_ids`、材料位置和“先核对是否相关”的提示，不立即要求重复上传。材料类型由调用方明确登记，当前不根据文件名或自由文本猜测。若已登记文件的类型有误，须按文件不可变版本机制登记更正版本并重新解析，不能直接覆盖旧文件元数据。

## 关联检查与证据目录

`check_evidence` 检查：

1. 配置中的事实缺口与字段值冲突。
2. 相同文件哈希及符合条件的文字近似重复候选。
3. 诉求是否关联可用事实、未排除材料及配置中的材料类型。
4. `Fact.evidence_ids` 与 `Evidence.proves_fact_ids` 是否一致。
5. 诉求关联的事实与材料之间是否已经登记对应关系。仅分别列出事实和文件不等于建立支持关系；有异议的事实不作为已可用的诉求支持。

检查只报告问题，不自动建立证明关系。补齐材料端的 `proves_fact_ids` 可使用已有更新接口；已有事实的来源字段不可直接改写，需要新增事实ID、明确确认取代关系，再更新材料及诉求引用。旧值和历史版本保留。

`build_evidence_directory` 沿用“用户已选择诉讼准备”条件。每项列出文件版本、原文位置、关联事实、诉求与拟证明事项；关联不一致、疑似重复、未知或未确认内容、尚无证明事项均有提示。没有任何规则缺口也不等于目录内容已经确认，仍须查看每项 `review_status` 与 `warnings`。已被取代的记载和已排除文件分别保留说明，不删除原件。

## 疑似重复的范围

原有字节哈希检查继续保留。本次新增 `material-text-similarity-0.1.0`，通过 `CaseService` 自动接入文件及提取存储：

1. 验证材料哈希与登记文件一致，原文引用能够回查；按明确提取版本读取各页已保存文字。
2. 只比较**类型相同、哈希不同**的材料；去除空白、规范化全角字符，原文保持不变。
3. 最少80个规范化字符、20种不同三字片段；所有数字序列必须一致，再检查三字片段Dice相似度是否达到0.92。
4. 命中后返回 `category="duplicate"`、`finding_id="similar_..."` 与 `field="evidence.possible_duplicate"` 的追问。相似度不是重复概率，不自动排除、合并或扣减金额。

这能提示部分“文件不同、已提取文字一致或近似”的重复上传。它不是图片视觉匹配：重拍、裁剪、旋转、手写识别差异、短票据或OCR数字错误可能漏检；不同文件套用同一表格也可能误报，因此必须核对原图和票据标识。

边界：最多100份材料、每份原始文字20000字符、2000组比对、100条候选；达到上限、文字不足、原文不可核实或提取版本不明确时，在 `limitations` 说明。不会截断长文后假装比对完整原件。比较的是已保存且被证据引用的提取页，解析时遗漏的页面不在本次覆盖范围内。

单独调用纯函数 `backend.case.materials.evidence.check_evidence` 而未注入文字读取器时，只做结构化与哈希检查，并明确报告没有执行文字近似检测。`checks_performed` 中仅实际比较过符合条件的材料组合才包含 `saved_material_text_similarity_v1`。

## Python调用与更新

原十个工具的请求/返回模型和注册保持不变。`CaseService` 新增可选的规则集依赖注入；默认读取上述配置。

```python
from backend.case import CaseService, SQLiteCaseRepository
from backend.case.materials.evidence_rules import load_evidence_rules

repo = SQLiteCaseRepository("var/cases.sqlite3")
service = CaseService(repo, evidence_rules=load_evidence_rules())
snapshot = repo.get_case(case_id)
context = {
    "request_id": "review-materials-01", "case_id": case_id,
    "experiment_run_id": None, "deadline_at": None,
}
result = service.check_evidence({
    "context": context,
    "case_version": snapshot.case_version,
    "case_snapshot": snapshot,
})
if result.data is not None:
    questions = result.data.suggested_questions
    missing = result.data.material_gaps
    duplicate_candidates = result.data.duplicates
    coverage = result.data.limitations
```

各类检查由 `Finding` 返回事实ID、材料ID和来源，便于页面定位和模型追问。`insufficient` 表示有待处理项；`ok` 仅表示已执行规则未发现问题，仍需阅读检查范围和未覆盖项。

用户明确确认重复后，沿用已有 `update_case`：被排除项设 `status="excluded"`、`duplicate_of` 指向保留项、`exclusion` 引用真实用户消息与确认身份。保存后重新检查。即使两文件哈希不同，明确确认仍可按现有流程排除其中一份；工具不会替用户提供确认。

注意：当前规则引擎支持对传入快照进行离线检查；应用仍须校验身份，并从共享仓库取得当前版本快照。目录保存沿用已有案件版本检查。模型不得自行提供身份、改写事实或虚构用户回复。

新增或修改材料规则时，需明确纠纷类型、诉求标识、材料类型、替代组、提示文字及规则来源，并增加相应版本和测试。尚未把其他纠纷或新的费用项目默认纳入本轮范围。

## 对接目录

| 对接人 | 成员三已提供 | 仍需对方完成 | 验收重点 |
|---|---|---|---|
| 成员1 | 问题列表、现有材料ID、来源位置、目录项和状态 | 原图对照、关联/排除操作、身份与消息保存、材料类型登记 | 不重复索要已有材料；确认后原件和历史仍可回查 |
| 成员2 | 可扩展规则配置与来源接口 | 正式材料依据、适用范围、版本及必要的专业核对 | 开发提示不冒充法定要求；新规则附可核实依据 |
| 成员4 | `suggested_questions`、缺口/冲突/重复结果、已有更新工具 | 真实追问、语义理解、确认等待、按新版本重跑 | 不自动关联或排除，不把相似度当作重复概率 |
| 成员5 | 新测试、合成闭环演示、规则与检测边界 | 独立材料、同票据不同照片/重扫及非重复近似表格的标注 | 分别测误报、漏报、补证命中和来源定位；开发例不算独立测试集 |

## 验证与剩余边界

2026-11-09本地验证：新增30项测试通过；全量196项，178项通过、18项因当前副本缺少成员四Runtime而跳过。原有40个工具交接场景仍通过，包含在本次回归中。

合成演示的11项检查通过：识别疑似重复、找到未关联的医疗票据、提示缺诊疗记录；模拟用户补充及明确关联/排除后，相应提示消失；其他未解决事实继续显示；原材料、历史版本和诉求金额保持完整。

```powershell
python -m unittest discover -s tests/case -p 'test_evidence_review.py'
python scripts/case/demo_evidence_review.py --output-dir var/evidence-review-demo
```

演示使用新目录，保存合成输入、SQLite演示库和 `result.json`。演示中的用户回复及诉讼路线选择均明确是模拟，不表示真实Agent已完成确认交互。

剩余：真实照片/手写材料的独立重复识别评测、复杂语义关联、真实性和法律证明力判断、正式法律材料依据及真实应用联调。当前没有实现通用图片视觉去重或对所有民事纠纷的完整证据规则。

代码：`backend/case/materials/evidence.py`、`evidence_rules.py`、`evidence_similarity.py`、`service.py`。
