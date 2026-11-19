"""Decimal-only calculations. No inferred liability percentages or reimbursement rules."""

from decimal import Decimal, InvalidOperation, localcontext
from datetime import datetime, timezone

from pydantic import ValidationError

from .contracts import CalculateCostsRequest, CalculationData
from ..models import BusinessError, BusinessResult, Money
from ..errors import CaseNotFound, VersionConflict
from .cost_summary import MATERIAL_SUMMARY_VERSION, MaterialAmountSummary, summarize_material_amounts
from .fee_rules import PROPERTY_FEE_RULE_VERSION, PropertyFeeRule
from .lending import PRINCIPAL_RULE_VERSION, principal_balance


class CostCalculator:
    def __init__(self, repository, *, assets=None):
        self.repository=repository
        self.assets=assets
        self.rules={}
        self.fee_rule=PropertyFeeRule()

    def register_rule(self, name, version, handler):
        if (name,version) in self.rules or (name == "property_case_acceptance_fee" and version == PROPERTY_FEE_RULE_VERSION) or version in {MATERIAL_SUMMARY_VERSION, PRINCIPAL_RULE_VERSION}:
            raise ValueError("calculation rule already registered")
        self.rules[(name,version)]=handler

    def calculate_costs(self, request):
        case_id,version=None,None
        try:
            request=CalculateCostsRequest.model_validate(request.model_dump(mode="json") if isinstance(request,CalculateCostsRequest) else request)
            case_id,version=request.context.case_id,request.case_version
            snapshot=self.repository.get_case(case_id)
            if snapshot.case_version!=version:
                raise VersionConflict(version,snapshot.case_version)
            if request.context.deadline_at and datetime.now(timezone.utc)>=request.context.deadline_at:
                return self._error(case_id,version,"timeout","费用计算请求已过期。")
            is_fee=request.calculation_type=="property_case_acceptance_fee" and request.rule_version==PROPERTY_FEE_RULE_VERSION
            is_material_v2=request.calculation_type in {"materials_total","materials_breakdown"} and request.rule_version==MATERIAL_SUMMARY_VERSION
            is_principal=request.calculation_type=="principal_balance" and request.rule_version==PRINCIPAL_RULE_VERSION
            if request.rule_version!="arithmetic_v1" and not is_fee and not is_material_v2 and not is_principal and (request.calculation_type,request.rule_version) not in self.rules:
                raise ValueError("unknown calculation rule/version; no silent latest-version fallback")
            assumptions=["此结果仅反映指定输入和规则；不自动认定法院支持金额或责任比例。"]
            unresolved=[]
            if is_principal:
                if request.rule_refs:
                    raise ValueError("principal arithmetic does not assert a legal rule source")
                amounts,formula,extra,unresolved=principal_balance(snapshot,request.inputs)
                assumptions+=extra
            elif is_fee:
                if snapshot.jurisdiction!="CN-Mainland":
                    raise ValueError("ordinary property fee rule applies only to explicit CN-Mainland cases")
                if request.rule_refs and request.rule_refs != [self.fee_rule.pointer]:
                    raise ValueError("fee rule_refs must match the pinned local rule source")
                request=request.model_copy(update={"rule_refs":[self.fee_rule.pointer]})
                amounts,formula,extra,unresolved=self.fee_rule.estimate(request.inputs)
                assumptions+=extra
            elif is_material_v2:
                if set(request.inputs)!={"fact_ids"}:
                    raise ValueError("material_amounts_v2 needs only fact_ids")
                def check_deadline():
                    if request.context.deadline_at and datetime.now(timezone.utc)>=request.context.deadline_at:
                        raise TimeoutError("material summary deadline expired")
                report=summarize_material_amounts(snapshot,request.inputs["fact_ids"],assets=self.assets,deadline_check=check_deadline)
                unresolved=report.unresolved_inputs
                amounts={} if report.total is None else {"total":report.total}
                if report.total is not None and request.calculation_type=="materials_breakdown":
                    amounts.update(report.known_category_subtotals)
                formula="total = sum(selected confirmed material occurrences after conflict/duplicate review); category subtotals partition total"
                assumptions+=report.limitations+[f"{row.fact_id}: {row.state}; {row.reason}" for row in report.rows]
            elif (request.calculation_type,request.rule_version) in self.rules:
                amounts,formula,extra,unresolved=self.rules[(request.calculation_type,request.rule_version)](request)
                assumptions+=extra
            elif request.calculation_type=="sum_amounts":
                if set(request.inputs)!={"amounts"}:
                    raise ValueError("sum_amounts needs only amounts")
                if not isinstance(request.inputs["amounts"],list):
                    raise ValueError("amounts must be a list")
                values=[Money.model_validate(item) for item in request.inputs["amounts"]]
                amounts={"total":self._sum(values)}
                formula="total = sum(input amounts)"
                assumptions.append("输入为调用方明确提供的金额；是否重复需先检查证据。")
            elif request.calculation_type=="subtract_amounts":
                if set(request.inputs) != {"base", "deductions"} or not isinstance(request.inputs["deductions"], list):
                    raise ValueError("subtract_amounts requires base (Money) and deductions (list of Money)")
                base = Money.model_validate(request.inputs["base"])
                deducted = self._sum([Money.model_validate(row) for row in request.inputs["deductions"]])
                if base.currency != deducted.currency:
                    raise ValueError("base and deductions must use the same currency")
                formula = "balance = base - sum(deductions)"
                amounts = {}
                if base.decimal < deducted.decimal:
                    unresolved.append("扣减合计超过基数，请核对金额和范围")
                else:
                    with localcontext() as ctx:
                        ctx.prec = max(len(base.amount), len(deducted.amount)) * 2 + 2
                        amounts["total"] = Money(amount=format(base.decimal - deducted.decimal, "f"), currency=base.currency)
                assumptions.append("差额仅针对明确列出的输入，未计入的未知项仍需核对。")
            elif request.calculation_type=="multiply":
                if set(request.inputs)!={"amount","multiplier"}:
                    raise ValueError("multiply needs amount and multiplier")
                value=Money.model_validate(request.inputs["amount"])
                multiplier=Money(amount=request.inputs["multiplier"],currency=value.currency).decimal
                with localcontext() as context:
                    context.prec=max(64,len(value.decimal.as_tuple().digits)+len(multiplier.as_tuple().digits)+2)
                    result=value.decimal*multiplier
                amounts={"total":Money(amount=format(result,"f"),currency=value.currency)}
                formula="total = amount * multiplier"
                assumptions.append("乘数由调用方明确提供，不代表自动认定责任比例。")
            elif request.calculation_type=="materials_total":
                if set(request.inputs)!={"fact_ids"} or not request.inputs["fact_ids"]:
                    raise ValueError("materials_total requires nonempty fact_ids")
                if len(set(request.inputs["fact_ids"]))!=len(request.inputs["fact_ids"]):
                    raise ValueError("fact_ids must be unique")
                facts={fact.fact_id:fact for fact in snapshot.facts}
                evidence={item.evidence_id:item for item in snapshot.evidence}
                values=[]
                groups={}
                for fact_id in request.inputs["fact_ids"]:
                    fact=facts.get(fact_id)
                    if fact is None:
                        raise ValueError("unknown fact_id")
                    if fact.value_type!="money" or fact.value is None or fact.superseded_by or fact.confirmation_status!="confirmed":
                        unresolved.append(fact_id+": needs active confirmed money fact")
                        continue
                    linked=[evidence[key] for key in fact.evidence_ids if evidence[key].status!="excluded"]
                    if not linked or len({item.content_sha256 for item in linked})!=1:
                        unresolved.append(fact_id+": needs one identified source document")
                        continue
                    value=Money.model_validate(fact.value)
                    key=(linked[0].content_sha256,fact.field.rsplit(".",1)[-1])
                    normalized=(value.decimal,value.currency)
                    if key in groups:
                        if groups[key]!=normalized:
                            unresolved.append(fact_id+": conflicting amount for identical material")
                        else:
                            assumptions.append(f"排除相同材料、相同金额项目的重复候选：{fact_id}")
                        continue
                    groups[key]=normalized
                    values.append(value)
                amounts={} if unresolved else {"total":self._sum(values)}
                formula="total = sum(confirmed selected material amounts, unique by document hash and item field)"
            else:
                raise ValueError("unsupported calculation_type")
            if request.context.deadline_at and datetime.now(timezone.utc)>=request.context.deadline_at:
                raise TimeoutError("calculation deadline expired")
            return BusinessResult[CalculationData](case_id=case_id,case_version=version,
                status="insufficient" if unresolved else "ok",summary="计算条件不足，未输出可能误导的总额。" if unresolved else "已按明确输入和版本化公式计算。",
                data=CalculationData(calculation_type=request.calculation_type,rule_version=request.rule_version,
                    amounts=amounts,formula=formula,inputs=request.inputs,assumptions=assumptions,
                    rule_refs=request.rule_refs,unresolved_inputs=unresolved),source_refs=request.rule_refs)
        except VersionConflict as exc:
            return self._error(case_id,exc.actual,"version_conflict",str(exc))
        except CaseNotFound:
            return self._error(case_id,version,"not_found","案件不存在。")
        except TimeoutError:
            return self._error(case_id,version,"timeout","费用计算请求已过期。")
        except (ValidationError,ValueError,TypeError,KeyError,InvalidOperation) as exc:
            return self._error(case_id,version,"invalid_request","计算参数或规则不合法。",{"reason":str(exc).splitlines()[0]})

    def summarize_materials(self, request):
        """Standalone row-level view; the existing calculation tool keeps its schema."""
        case_id,version=None,None
        try:
            request=CalculateCostsRequest.model_validate(request.model_dump(mode="json") if isinstance(request,CalculateCostsRequest) else request)
            case_id,version=request.context.case_id,request.case_version
            if request.calculation_type!="materials_breakdown" or request.rule_version!=MATERIAL_SUMMARY_VERSION or set(request.inputs)!={"fact_ids"} or request.rule_refs:
                raise ValueError("summary requires materials_breakdown/material_amounts_v2, fact_ids and no legal rule_refs")
            snapshot=self.repository.get_case(case_id)
            if snapshot.case_version!=version:
                raise VersionConflict(version,snapshot.case_version)
            def check_deadline():
                if request.context.deadline_at and datetime.now(timezone.utc)>=request.context.deadline_at:
                    raise TimeoutError("material summary deadline expired")
            check_deadline()
            data=summarize_material_amounts(snapshot,request.inputs["fact_ids"],assets=self.assets,deadline_check=check_deadline)
            check_deadline()
            return BusinessResult[MaterialAmountSummary](case_id=case_id,case_version=version,
                status="insufficient" if data.unresolved_inputs else "ok",data=data,
                summary="已列出金额分类、计入与未计入原因；总额仅在选定材料条件齐备时提供。")
        except VersionConflict as exc:
            return self._error(case_id,exc.actual,"version_conflict",str(exc))
        except CaseNotFound:
            return self._error(case_id,version,"not_found","案件不存在。")
        except TimeoutError:
            return self._error(case_id,version,"timeout","费用汇总请求已过期。")
        except (ValidationError,ValueError,TypeError,KeyError,InvalidOperation) as exc:
            return self._error(case_id,version,"invalid_request","汇总参数不合法。",{"reason":str(exc).splitlines()[0]})

    @staticmethod
    def _sum(values):
        if not values:
            raise ValueError("at least one amount is required; unknown is not zero")
        if len({value.currency for value in values})!=1:
            raise ValueError("mixed currencies cannot be added without an explicit conversion rule")
        with localcontext() as context:
            context.prec=max(64,max(value.decimal.adjusted() for value in values)-
                min(value.decimal.as_tuple().exponent for value in values)+len(str(len(values)))+4)
            total=sum((value.decimal for value in values),Decimal("0"))
        return Money(amount=format(total,"f"),currency=values[0].currency)

    @staticmethod
    def _error(case_id,version,code,message,details=None):
        return BusinessResult[CalculationData](case_id=case_id,case_version=version,status="error",summary=message,data=None,
            error=BusinessError(code=code,message=message,details=details))
