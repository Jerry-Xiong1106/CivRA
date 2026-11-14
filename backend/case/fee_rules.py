"""Pinned ordinary-property filing fee estimate with an inspectable local rule source."""

from datetime import date
from decimal import Decimal, ROUND_HALF_UP, localcontext
import hashlib
import json
from pathlib import Path
from typing import Literal
import re

from pydantic import field_validator

from .models import ContractModel, Money, SourcePointer


PROPERTY_FEE_RULE_VERSION = "cn_property_acceptance_fee_2007_v1"
LOCAL_SOURCE_PREFIX = "member3.local_rule."


class PropertyFeeInputs(ContractModel):
    claim_value: Money
    case_category: Literal["ordinary_property"]
    procedure: Literal["first_instance_ordinary"]
    adjustment: Literal["none"]
    filing_date: date

    @field_validator("filing_date", mode="before")
    @classmethod
    def explicit_date(cls, value):
        if type(value) is date or isinstance(value, str) and re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", value):
            return value
        raise ValueError("filing_date must be a date or YYYY-MM-DD string, not a timestamp")


class PropertyFeeRule:
    def __init__(self):
        path = Path(__file__).resolve().parents[2] / "data/config/member3_property_fee_rule.json"
        self.record = json.loads(path.read_text(encoding="utf-8"))
        if self.record["rule_version"] != PROPERTY_FEE_RULE_VERSION:
            raise ValueError("unexpected fee rule version")

    @property
    def pointer(self):
        # Results and callers may hold mutable models; never expose shared registry state.
        return SourcePointer(source_id=self.record["source_id"], source_version=self.record["source_version"])

    def resolve(self, pointer):
        pointer = SourcePointer.model_validate(pointer)
        if pointer != self.pointer:
            raise KeyError("local fee source identity/version not found")
        record = self.record
        return {"source_id": pointer.source_id, "version": pointer.source_version, "source_type": "statute",
                "title": record["title"], "locator": record["locator"], "url": record["url"],
                "text": record["source_text"], "excerpt": record["source_text"],
                "effective_from": record["effective_from"], "effective_to": None, "verified_on": record["verified_on"],
                "jurisdiction": "CN-Mainland", "source_note": record["source_note"],
                "text_scope": "short_excerpt_only",
                "text_sha256": hashlib.sha256(record["source_text"].encode()).hexdigest()}

    def estimate(self, inputs):
        data = PropertyFeeInputs.model_validate(inputs)
        amount = data.claim_value.decimal
        if data.claim_value.currency != "CNY" or amount <= 0:
            raise ValueError("fee estimate needs an explicitly positive CNY claim value")
        if amount.as_tuple().exponent < -2:
            raise ValueError("claim value accepts at most two decimal places")
        effective = date.fromisoformat(self.record["effective_from"])
        checked = date.fromisoformat(self.record["verified_on"])
        assumptions = [
            "按调用方明确选择的普通财产案件、一审普通程序、未调整标准估算；不自动判断案件分类。",
            "不处理减免、减半、反诉、上诉、再审、非财产案件、保全执行等其他费用或费用最终负担。",
            "诉讼请求金额须由调用方明确提供，不自动等同于票据总额或法院支持金额。",
            "fee_exact为分段累计原值；fee_estimate仅按ROUND_HALF_UP保留两位小数展示，此取整是工程展示约定，缴费以法院通知为准。",
            f"规则来源：{self.record['url']}；核对日期{checked.isoformat()}，该日期不是法律失效日期。",
            "本地来源读取器仅提供短引文与定位；分段数据保存在版本化规则配置中，完整原文仍需从官方来源或成员二读取。",
        ]
        formula = "fee_exact = 50 CNY + sum(max(0, min(claim_value, upper_i) - lower_i) * rate_i); fee_estimate = round_half_up(fee_exact, 0.01)"
        if not effective <= data.filing_date <= checked:
            return {}, formula, assumptions, ["filing_date: 超出本地规则已核对的适用日期范围，需要重新核实来源后选择规则版本"]
        with localcontext() as context:
            context.prec = max(64, len(amount.as_tuple().digits)+12)
            total = Decimal(self.record["base_fee"])
            lower = Decimal(self.record["base_limit"])
            assumptions.append("基础段：不超过10000元部分按每件50元计。")
            for band in self.record["bands"]:
                upper = Decimal(band["upper"]) if band["upper"] is not None else amount
                portion = max(Decimal("0"), min(amount, upper)-lower)
                if portion:
                    increment = portion * Decimal(band["rate"])
                    total += increment
                    assumptions.append(f"超过{lower}元至{band['upper'] or '无上限'}元段：{portion} × {band['rate']} = {increment}元。")
                if amount <= upper:
                    break
                lower = upper
            rounded = total.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        return {"fee_exact": Money(amount=format(total, "f"), currency="CNY"),
                "fee_estimate": Money(amount=format(rounded, "f"), currency="CNY")}, formula, assumptions, []
