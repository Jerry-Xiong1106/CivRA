"""Reviewable material amounts; unknown values and unresolved duplication are not totals."""

from collections import defaultdict
from decimal import Decimal, localcontext
from typing import Literal

from ..materials.evidence_similarity import inspect_text_similarity, read_material_text
from ..models import ContractModel, MaterialRef, Money


MATERIAL_SUMMARY_VERSION = "material_amounts_v2"
CATEGORY_LABELS = {"repair_amount": "维修费", "medical_amount": "医疗费", "towing_amount": "拖车费", "appraisal_amount": "鉴定费"}


class MaterialAmountRow(ContractModel):
    fact_id: str
    category: str
    label: str
    amount: Money | None
    state: Literal["included", "duplicate", "excluded", "superseded", "unresolved"]
    reason: str
    evidence_ids: list[str]
    material_refs: list[MaterialRef]
    duplicate_of_fact_id: str | None = None


class MaterialAmountSummary(ContractModel):
    rule_version: str = MATERIAL_SUMMARY_VERSION
    rows: list[MaterialAmountRow]
    known_category_subtotals: dict[str, Money]
    total: Money | None
    unresolved_inputs: list[str]
    limitations: list[str]


def sum_money(values):
    if not values or len({item.currency for item in values}) != 1:
        raise ValueError("a sum requires nonempty amounts in one currency")
    with localcontext() as context:
        context.prec = max(64, max(item.decimal.adjusted() for item in values)
                           - min(item.decimal.as_tuple().exponent for item in values)+len(str(len(values)))+4)
        total = sum((item.decimal for item in values), Decimal("0"))
    return Money(amount=format(total, "f"), currency=values[0].currency)


def summarize_material_amounts(snapshot, fact_ids, *, assets=None, deadline_check=None):
    if not isinstance(fact_ids, list) or not fact_ids or any(not isinstance(key, str) for key in fact_ids):
        raise ValueError("fact_ids must be a nonempty list of strings")
    if len(set(fact_ids)) != len(fact_ids):
        raise ValueError("fact_ids must be unique")
    facts = {fact.fact_id: fact for fact in snapshot.facts}
    evidence = {item.evidence_id: item for item in snapshot.evidence}
    if set(fact_ids)-facts.keys():
        raise ValueError("unknown fact_id")
    active = [fact for fact in snapshot.facts if not fact.superseded_by and
              (not fact.evidence_ids or any(evidence[key].status != "excluded" for key in fact.evidence_ids))]
    rows, groups = [], defaultdict(list)
    for fact_id in fact_ids:
        if deadline_check:
            deadline_check()
        fact = facts[fact_id]
        category = fact.field.rsplit(".", 1)[-1]
        value = Money.model_validate(fact.value) if fact.value_type == "money" and fact.value is not None else None
        row = MaterialAmountRow(fact_id=fact_id, category=category, label=CATEGORY_LABELS.get(category, category),
            amount=value, state="included", reason="已确认材料金额，等待本次一致性及重复检查。",
            evidence_ids=list(fact.evidence_ids), material_refs=list(fact.material_refs))
        rows.append(row)
        linked = [evidence[key] for key in fact.evidence_ids if evidence[key].status != "excluded"]
        if fact.superseded_by:
            row.state, row.reason = "superseded", "原事实已被明确取代，未计入。"
        elif fact.evidence_ids and not linked:
            row.state, row.reason = "excluded", "关联材料已明确排除，未计入。"
        elif value is None or category not in CATEGORY_LABELS or fact.confirmation_status != "confirmed":
            row.state, row.reason = "unresolved", "需要支持类别内的已确认金额事实；未知、待确认及其他金额用途不计入。"
        elif any(other.field == fact.field and other.value_type == "money" and other.value is not None
                 and (Money.model_validate(other.value).decimal, other.value["currency"]) != (value.decimal, value.currency)
                 for other in active):
            row.state, row.reason = "unresolved", "相同事实字段仍有不同金额候选，需要明确解决冲突。"
        elif not linked or len({item.content_sha256 for item in linked}) != 1:
            row.state, row.reason = "unresolved", "需要可明确识别的一份来源材料。"
        else:
            file_keys = {(item.file_id, item.file_version) for item in linked}
            refs = [ref for ref in fact.material_refs if (ref.file_id, ref.file_version) in file_keys]
            if not refs or any(ref.span_start is None for ref in refs):
                row.state, row.reason = "unresolved", "缺少可区分金额记录的原文区间，不能把同文件多条金额直接去重或相加。"
                continue
            if assets is not None:
                try:
                    for ref in refs:
                        if ref.extraction_version:
                            assets.read_extraction(snapshot.case_id, ref)
                except (ValueError, LookupError):
                    row.state, row.reason = "unresolved", "金额原文无法按保存的提取版本回查。"
                    continue
            location = tuple(sorted({(ref.page or 0, ref.paragraph or "", ref.span_start, ref.span_end, ref.quote or "") for ref in refs}))
            groups[(linked[0].content_sha256, category, location)].append(row)
    occurrences = defaultdict(set)
    for digest, category, location in groups:
        occurrences[(digest, category)].add(location)
    for (digest, category, _), group in groups.items():
        if len(occurrences[(digest, category)]) > 1:
            for row in group:
                row.state, row.reason = "unresolved", "同一原文件的同类金额出现在多个位置，须明确独立票据项目或拆分材料；未自动合并或相加。"
            continue
        if len({(row.amount.decimal, row.amount.currency) for row in group}) > 1:
            for row in group:
                row.state, row.reason = "unresolved", "相同材料与金额位置出现不同金额，不能选取其中一个。"
        else:
            retained = min(group, key=lambda row: row.fact_id)
            retained.reason = "已确认的明确金额记录，纳入本次分类汇总。"
            for row in group:
                if row is not retained:
                    row.state, row.reason, row.duplicate_of_fact_id = "duplicate", "相同文件字节、金额项目和原文位置，重复候选未重复计入。", retained.fact_id
    selected_sources = {key for row in rows if row.state == "included" for key in row.evidence_ids if evidence[key].status != "excluded"}
    reader = (lambda item: read_material_text(assets, snapshot.case_id, item)) if assets is not None else None
    similar, _, _, limits = inspect_text_similarity([evidence[key] for key in sorted(selected_sources)], reader,
                                                   deadline_check=deadline_check)
    for finding in similar:
        for row in rows:
            if row.state == "included" and set(row.evidence_ids) & set(finding.evidence_ids):
                row.state, row.reason = "unresolved", "相关材料文字疑似重复，须先确认保留/排除或说明并非重复，再汇总。"
    buckets = defaultdict(list)
    for row in rows:
        if row.state == "included":
            buckets[f"{row.category}.{row.amount.currency}"].append(row.amount)
    known = {key: sum_money(values) for key, values in sorted(buckets.items())}
    unresolved = [f"{row.fact_id}: {row.reason}" for row in rows if row.state == "unresolved"]
    currencies = {item.currency for item in known.values()}
    if not known:
        unresolved.append("selected_materials: 没有可计入的已确认材料金额；未知不记为零")
    elif len(currencies) > 1:
        unresolved.append("currency: 多币种分别列出已知小计，不生成跨币种总额")
    total = sum_money(list(known.values())) if known and not unresolved else None
    return MaterialAmountSummary(rows=rows, known_category_subtotals=known, total=total, unresolved_inputs=unresolved,
        limitations=["汇总仅针对明确选中的材料金额；不代表全部损失、诉讼请求金额或法院支持金额。",
                     "不混入受理费、模型费用或未知费用；不推断责任比例、保险抵扣、利息或赔偿标准。",
                     "同一PDF中不同原文位置保留记录，但不自动认定为独立票据；同类多位置或相同字段金额冲突须先澄清或拆分材料。",
                     "文字相似度检测有限；已知小计只覆盖included行，存在未解决项时不提供总额。", *limits])
