"""Case-grounded comparison, without route ranking or semantic legal conclusions."""

from datetime import datetime, timezone
from ..business_support import unique
from pydantic import ValidationError

from .contracts import CompareRoutesData, CompareRoutesRequest, RouteOption
from ..case_context import build_case_context
from ..materials.evidence import check_evidence
from ..calculation.fee_rules import PROPERTY_FEE_RULE_VERSION, PropertyFeeInputs, PropertyFeeRule
from ..models import BusinessError, BusinessResult, Money
from ..research.sources import SourceReviewRequest, SourceSelection
from .route_models import (
    COMPARISON_VERSION, RouteCostItem, RouteDimension,
    RouteGoalFocus, RouteSourceCheck,
)


DIMENSIONS = ("materials", "costs", "time_steps", "agreement_performance", "risks")
GOALS = {
    "节省成本": ["costs"], "降低费用": ["costs"], "low_cost": ["costs"],
    "尽快解决": ["time_steps"], "节省时间": ["time_steps"], "speed": ["time_steps"],
    "保障履行": ["agreement_performance", "risks"], "enforceability": ["agreement_performance", "risks"],
    "减少冲突": ["agreement_performance"], "维持关系": ["agreement_performance"],
    "证据不足": ["materials", "risks"], "了解风险": ["risks"],
}
MATERIALS = {
    "negotiation": "用于沟通的诉求、金额和材料摘要；缺口不被隐藏，也不自动阻止双方沟通。",
    "mediation": "供拟选调解渠道核对的案情、诉求和现有材料；具体材料要求需向该机构核实。",
    "litigation": "供诉讼准备核对的事实—诉求—材料关系及补充清单；规则提醒不等于法定立案条件或法院采信结论。",
}
AGREEMENTS = {
    "negotiation": "待核对协商条款、金额、履行时间、履行凭证及未履行后的可用程序。",
    "mediation": "先核实调解类型及机构，再区分调解协议、司法确认和后续履行/执行的适用条件；不将各种调解结果等同。",
    "litigation": "待核对实际裁判/调解文书、效力状态、履行情况及后续申请条件；获得结果不代表已实际收款。",
}
TIMES = {
    "negotiation": "按准备、沟通和履行核对组织步骤；回复与履行时间需双方明确，当前无确定办结时间。",
    "mediation": "按渠道核实、参与调解和结果核对组织步骤；具体排期、期限及起算待核实。",
    "litigation": "按管辖核实、材料准备、草稿核验和提交准备组织步骤；公开程序期限与本案实际办结时间需要分别说明。",
}
RISKS = {
    "negotiation": "待确认对方沟通意愿、争议范围、方案内容及履行能力；沟通无结果时仍需由用户另作选择。",
    "mediation": "待确认双方意愿、渠道适用性、协议条件及履行情况；不预设一定达成协议。",
    "litigation": "待核对证据缺口、法律适用、实际费用和履行情况；不计算未经校准的胜诉概率。",
}


def _deadline(request):
    if request.context.deadline_at and datetime.now(timezone.utc) >= request.context.deadline_at:
        raise TimeoutError("route comparison deadline expired")


def _fee_items(request):
    """Replay pinned rules; never sum exact/rounded forms, invoices or alternative scenarios."""
    rule, candidates, issues = PropertyFeeRule(), {}, []
    fee_count = 0
    for index, calculation in enumerate(request.calculations):
        _deadline(request)
        if calculation.calculation_type != "property_case_acceptance_fee":
            issues.append(f"calculations[{index}]: {calculation.calculation_type}未映射为路线支出；损失或算术总额不等于维权费用。")
            continue
        fee_count += 1
        try:
            if request.case_snapshot.jurisdiction != "CN-Mainland":
                raise ValueError("需明确中国内地管辖范围")
            if calculation.rule_version != PROPERTY_FEE_RULE_VERSION:
                raise ValueError("未支持的受理费规则版本")
            if calculation.unresolved_inputs:
                raise ValueError("费用计算仍有未解决输入")
            if calculation.rule_refs != [rule.pointer]:
                raise ValueError("费用来源ID或版本与固定规则不符")
            inputs = PropertyFeeInputs.model_validate(calculation.inputs)
            amounts, formula, assumptions, unresolved = rule.estimate(calculation.inputs)
            if unresolved:
                raise ValueError("；".join(unresolved))
            if calculation.formula != formula or set(calculation.amounts) != set(amounts) or any(
                calculation.amounts[key].decimal != value.decimal or calculation.amounts[key].currency != value.currency
                for key, value in amounts.items()
            ):
                raise ValueError("费用原值、估算值或公式与当前规则重算结果不一致")
            # Decimal spelling differences do not create a second fee scenario.
            key = (inputs.claim_value.decimal, inputs.claim_value.currency, inputs.filing_date,
                   inputs.case_category, inputs.procedure, inputs.adjustment)
            if key in candidates:
                candidates[key].calculation_indexes.append(index)
            else:
                candidates[key] = RouteCostItem(kind="acceptance_fee_estimate", label="普通财产案件受理费基础估算",
                    amount=amounts["fee_estimate"], source_refs=[rule.pointer], calculation_indexes=[index],
                    assumptions=assumptions + [
                        "本次重新核对输入、公式及金额；原CalculationData无案件版本，不据此证明输入来自本案或已经用户确认。",
                        f"调用方估算条件：请求金额{inputs.claim_value.amount} CNY；拟提交日期{inputs.filing_date.isoformat()}。",
                        "仅列fee_estimate一次；fee_exact是同一费用的精确表示，不重复计入。",
                    ])
        except (ValidationError, ValueError, TypeError, KeyError) as exc:
            issues.append(f"calculations[{index}]: 未采用受理费结果：{str(exc).splitlines()[0]}")
    if len(candidates) > 1:
        issues.append("存在多组不同受理费估算条件，请明确选择一组；未相加也未自动选择。")
        return [], issues
    # If any fee scenario was rejected, do not silently choose the remaining one.
    accepted_count = sum(len(item.calculation_indexes) for item in candidates.values())
    if fee_count != accepted_count:
        if candidates:
            issues.append("同时有无法核实的受理费候选，待调用方处理后重新提交；暂不展示单一受理费。")
        return [], issues
    return list(candidates.values()), issues


def _expense_items(request, context):
    facts = {fact.fact_id: fact for fact in request.case_snapshot.facts}
    items, seen, issues = {route: [] for route in MATERIALS}, set(), []
    for index, expense in enumerate(request.user_expenses):
        fact = facts.get(expense.fact_id)
        if fact is None or fact.fact_id not in context.confirmed_fact_ids or fact.value_type != "money" or fact.provenance != "user_statement":
            issues.append(f"user_expenses[{index}]: 需要本案有效、无冲突、已确认的用户陈述金额事实，未采用。")
            continue
        # One saved fact can appear in alternative routes, but only once per route.
        key = (expense.route, fact.fact_id)
        if key in seen:
            issues.append(f"user_expenses[{index}]: 同一路线重复引用金额事实{fact.fact_id}，仅列一次。")
            continue
        seen.add(key)
        items[expense.route].append(RouteCostItem(kind="user_declared_expense", label=expense.label,
            amount=Money.model_validate(fact.value), fact_ids=[fact.fact_id], assumptions=[
                "用户已确认的自述金额；路线归属和用途由调用方指定，尚需用户复核，不是法定收费结论。",
                "不同事实可能仍指同一支出；未自动合计，也未进行货币换算。",
            ]))
    return items, issues


def _source_checks(request, reviewer):
    checks, issues, cards, rejected = [], [], {}, {}
    if request.source_bindings and reviewer is not None:
        selections = [SourceSelection(source_ref=item.source_ref, expected_kind="statute", quote=item.quote)
                      for item in request.source_bindings]
        result = reviewer(SourceReviewRequest(context=request.context, case_version=request.case_version, selections=selections))
        _deadline(request)
        if result.status == "error":
            if result.error and result.error.code == "timeout":
                raise TimeoutError("source review deadline expired")
            issues.append("来源核对未完成，保留候选陈述，不作为确定法律结论。")
        else:
            for card in result.data.statutes:
                cards[(card.source_ref.source_id, card.source_ref.source_version, card.quote)] = card
            for item in result.data.rejected:
                ref = item["source_ref"]
                rejected[(ref["source_id"], ref["source_version"])] = item["reason"]
    for binding in request.source_bindings:
        card = cards.get((binding.source_ref.source_id, binding.source_ref.source_version, binding.quote))
        reason = "已核对精确版本原文片段及定位；尚未验证候选陈述的语义支持、完整适用条件和本案适用性。" if card else (
            rejected.get((binding.source_ref.source_id, binding.source_ref.source_version), "未接入原文读取器或未能核对该片段"))
        checks.append(RouteSourceCheck(binding_id=binding.binding_id, route=binding.route, dimension=binding.dimension,
            proposed_statement=binding.statement, conditions=binding.conditions,
            status="original_text_checked" if card else "rejected", source=card.model_dump() if card else None, reason=reason))
        issues.append(f"source_bindings[{binding.binding_id}]: {reason}")
        if card:
            issues.extend(f"source_bindings[{binding.binding_id}]: {warning}" for warning in card.warnings)
    bound = [item.source_ref for item in request.source_bindings]
    for pointer in unique(request.available_authorities):
        if pointer not in bound:
            issues.append(f"来源{pointer.source_id}@{pointer.source_version}尚未绑定到具体路线、维度和原文片段。")
    return checks, issues


def _goals(request):
    result = []
    for goal in unique(request.user_goals + request.case_snapshot.user_goals):
        origin = "both" if goal in request.user_goals and goal in request.case_snapshot.user_goals else (
            "request" if goal in request.user_goals else "case")
        dimensions = GOALS.get(goal.strip(), [])
        result.append(RouteGoalFocus(goal=goal, origin=origin, dimensions=dimensions,
            question="请结合这些比较维度核对目标、可接受条件和取舍；当前不作路线优劣排序。" if dimensions else
                     "该目标尚未匹配明确比较维度，请用户或成员四澄清含义；不通过关键词猜测偏好。"))
    return result


def compare_routes(payload, *, evidence_checker=None, source_reviewer=None):
    case_id, version = None, None
    try:
        request = CompareRoutesRequest.model_validate(payload.model_dump(mode="json") if hasattr(payload, "model_dump") else payload)
        case_id, version = request.context.case_id, request.case_version
        _deadline(request)
        snapshot = request.case_snapshot
        report = (evidence_checker or check_evidence)({"context": request.context, "case_version": version, "case_snapshot": snapshot})
        if report.status == "error":
            return BusinessResult(case_id=case_id, case_version=version, status="error", data=None,
                                  summary="证据检查未完成，未生成路线比较。", error=report.error)
        context = build_case_context(snapshot, report.data)
        fees, cost_issues = _fee_items(request)
        expenses, expense_issues = _expense_items(request, context)
        cost_issues += expense_issues
        sources, source_issues = _source_checks(request, source_reviewer)
        goals = _goals(request)
        base_unknowns = ["所在地办理机构、具体入口及本案适用条件待核实。",
                         "公开程序期限、起算条件、排期和实际履行时间未确定；未生成办结天数。",
                         "各路线全部支出未知；已列金额不是总费用，未列费用不能解释为零。",
                         "通用步骤是准备提示，未完成逐条法律适用和语义支持审查。"]
        unresolved = base_unknowns + cost_issues + source_issues
        if not request.available_authorities and not fees:
            # Preserve the existing handoff contract's explicit missing-source message.
            unresolved.append("当前未提供可追溯的法律或程序依据。")
        if context.findings or context.pending_fact_ids:
            unresolved.append("本案仍有证据规则问题或待核对事实，请查看case_context及对应比较维度。")
        if not context.claim_ids:
            unresolved.append("尚无有效诉求记录，需先明确希望解决的事项。")
        if not fees:
            unresolved.append("尚无唯一且通过重算的普通财产案件受理费估算，诉讼费用保持未知。")
        if not sources:
            unresolved.append("尚无逐条绑定的程序原文；受理费规则只支持费用计算，不支持其他程序结论。")
        if not goals or any(not item.dimensions for item in goals):
            unresolved.append("用户目标为空或仍需澄清，未自动生成目标排序或最优路线。")
        from .routes import TEMPLATES  # Workflow templates stay unchanged for function 7.
        options = []
        finding_ids = [item.finding_id for item in context.findings]
        for route, template in TEMPLATES.items():
            route_sources = [item for item in sources if item.route == route and item.source is not None]
            costs = (fees if route == "litigation" else []) + expenses[route]
            refs = unique([ref for item in costs for ref in item.source_refs] + [item.source.source_ref for item in route_sources])
            text = {"materials": MATERIALS[route], "costs": "仅列已核对的受理费估算和用户明确提供的支出，分项显示，不合计为路线总价。",
                    "time_steps": TIMES[route], "agreement_performance": AGREEMENTS[route], "risks": RISKS[route]}
            dimensions = []
            for dimension in DIMENSIONS:
                questions = []
                if dimension == "materials":
                    questions = [item.text for item in report.data.suggested_questions]
                    if context.pending_fact_ids:
                        questions.append("请核对pending_fact_ids中的事实；未确认或冲突内容不能作为确定前提。")
                elif dimension == "costs":
                    questions = ["请核对费用用途、预计/实际性质、币种及是否重复；其他支出仍需补充。"]
                    if route == "mediation":
                        questions.append("具体是哪类调解、哪个机构、哪些收费项目？未确认前不将调解总费用写成零。")
                elif dimension == "time_steps":
                    questions = ["有哪些已知日期、排期和可接受等待时间？程序期限的依据和起算事件是什么？"]
                elif dimension == "agreement_performance":
                    questions = ["已有哪些实际协议或结果文书？约定、实际履行及后续程序条件是否核实？"]
                else:
                    questions = ["哪些证据问题尚未解决？对方意愿、履行能力和可接受取舍有哪些已确认信息？"]
                dimensions.append(RouteDimension(dimension=dimension, summary=text[dimension],
                    fact_ids=context.confirmed_fact_ids if dimension in {"materials", "risks"} else [],
                    evidence_ids=context.evidence_ids if dimension in {"materials", "risks"} else [],
                    finding_ids=finding_ids if dimension in {"materials", "risks"} else [],
                    source_binding_ids=[item.binding_id for item in route_sources if item.dimension == dimension], questions=questions))
            options.append(RouteOption(route=route, prerequisites=list(template[0][3]), steps=[item[1] for item in template],
                costs=[item.amount for item in costs], cost_items=costs, source_refs=refs, dimensions=dimensions,
                uncertainties=unique(base_unknowns + ["本案证据问题请查看case_context.findings；材料存在不代表已被采信。"] +
                    [item.reason for item in sources if item.route == route])))
        _deadline(request)
        data = CompareRoutesData(implementation_version=COMPARISON_VERSION, options=options, case_context=context,
            source_checks=sources, goal_focus=goals, cost_review_issues=cost_issues, unresolved_items=unique(unresolved),
            assumptions=["协商、调解、诉讼是并列选项，不要求依次完成；本工具不选择或切换路线。",
                         "五个维度对应讨论稿补充；材料检查是工程提示，不是法院强制清单。",
                         "目标映射仅用于提示重点，无评分、胜诉概率或自动最优路线。",
                         "source_refs表示可追溯来源，不表示候选陈述已通过语义或法律适用核验。",
                         "costs是cost_items金额的兼容投影，两处不能重复相加；各项并未构成完整总费用。"])
        return BusinessResult[CompareRoutesData](case_id=case_id, case_version=version, status="insufficient",
            summary="已按本案材料和费用输入生成三路线、五维度比较草稿，待核实事项已保留。", data=data,
            source_refs=unique([ref for option in options for ref in option.source_refs]))
    except TimeoutError:
        return _error(case_id, version, "timeout", "路线比较请求已过期。")
    except (ValidationError, ValueError, TypeError, KeyError) as exc:
        return _error(case_id, version, "invalid_request", str(exc).splitlines()[0])


def _error(case_id, version, code, message):
    return BusinessResult(case_id=case_id, case_version=version, status="error", data=None,
        summary="路线比较未完成。", error=BusinessError(code=code, message=message))
