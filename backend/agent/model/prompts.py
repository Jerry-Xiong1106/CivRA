"""System prompt and per-call context assembly."""

from __future__ import annotations

import json
from typing import Any

from langchain_core.messages import BaseMessage, HumanMessage, SystemMessage, ToolMessage
from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder

from backend.agent.case_port import CaseSnapshot
from backend.agent.model.llm import message_dict

ROUTE_NAMES = {"negotiation": "协商解决", "mediation": "调解解决", "litigation": "诉讼准备"}

SYSTEM_PROMPT = """你是 CivRA，帮助中国内地用户理解和处理民事纠纷。
区分用户陈述、材料记载和已核实事实。不知道、未提供或未上传的保留未知，不补造事实、法律依据或外部处理结果；只有用户明确否认才记为没有。
需要法律依据时检索并核对适用范围，只引用工具实际返回的来源。工具返回候选不代表法律结论成立；失败或信息不足时说明实际限制，不盲目重复调用，错误参数按工具返回修正。
只问推进当前事项必需、且工具查不到的信息，一次问最关键的一两个；已知的不重问。先读已上传的材料，再问缺口。recent_uploaded_files 只说明文件已收到，需要内容时调用 parse_documents。
涉及保存、生成、核验等操作，只有工具成功执行后才称已完成。不给出无依据的胜诉概率。
回答用中文，结构清楚，可以使用 Markdown。"""

CONSULTATION_INSTRUCTIONS = """当前阶段：了解情况（用户尚未选择处理路线）。
回答用户的问题。了解到用户面对的是现实纠纷后，可调用 compare_routes 比较协商、调解、诉讼三条路线，结合本案给出建议，并提醒用户在界面上点击对应的路线入口开始办理。不要声称已经进入某条路线。"""

ROUTE_INSTRUCTIONS = """当前路线：{route_name}。你要主动引导用户按步骤把这条路线走完。
当前步骤：{step_title}（第 {step_number}/{step_total} 步，step_id={step_id}）
本步交付：{deliverable}
需要的材料：{materials}
完成标准：{condition}
本步常用工具：{tools}
{completed}
做法：
1. 围绕当前步骤推进：先用已有信息和工具产出本步交付物；缺少关键信息时用 ask_user 追问。
2. 本步交付物给出后，调用 confirm_step（step_id={step_id}）请用户确认：answer 写给用户的完整回答（含交付物），summary 概括本步结果。界面会显示“完成／还需修改”按钮，用户确认后系统记录进度，你接着引导下一步。
   请用户确认只能用 confirm_step，不要在 finish 里写“请确认后进入下一步”——那样无法记录进度。需要用户补充信息或材料时用 ask_user。只有本步要等用户在现实中办完某件事（如与对方沟通、递交材料）且暂时无法确认，或用户问的是别的问题时，才用 finish。
3. 不替用户确认，不跳步。用户问别的问题或想先做别的事时，先回答，再把话题带回当前步骤。
4. 用户说这一步还需修改时，按用户意见修改后再请求确认。"""

ROUTE_FINISHED = """当前路线：{route_name}，所有步骤都已完成。
{completed}
总结已完成的事项和成果，说明用户之后可能需要关注的事；用户提出新需求时照常处理。"""

RUNTIME_TEMPLATE = """当前运行限制（由程序提供）：
{runtime_context}
检索语料范围：已登记的民法典、民事诉讼法、道路交通安全法版本；不含完整类案库和所有程序、收费依据。超出范围时说明限制。
"""

def _fact_field_guide() -> str:
    """Canonical fact names come from member 3's templates and evidence rules, not a copy here."""
    from backend.case.drafting.templates import COMMON_FIELDS, PROFILES
    from backend.case.materials.evidence import FIELD_LABELS

    def pairs(fields):
        return "，".join(f"{key}={label}" for key, label in fields.items())

    lines = ["通用：" + pairs(COMMON_FIELDS)]
    lines += [f"{label}（{dispute}）：" + pairs(fields) for dispute, label, _, fields in PROFILES]
    amounts = {key: label for key, label in FIELD_LABELS.items() if key.endswith("_amount")}
    lines.append("金额明细（money）：" + pairs(amounts))
    return "\n".join(lines)


FACT_RECORDING = """记录用户陈述的事实：案件状态 statements 中的用户陈述含有与纠纷有关的具体事实（日期、地点、当事人、金额、责任认定、保险、借款或合同情况等），而 facts 中尚无对应的有效事实时，先调用一次 update_case 把本轮新事实一并保存，再做其他操作。
每条事实：field 用下列规范字段名（没有合适的才自拟简短英文名）；provenance="user_statement"；statement_refs 填载有该陈述的 statements[].message_id；fact_id 自拟且在案件内唯一（如 fact_accident_date_1）。
确认：默认 confirmation_status="unconfirmed"。用户明确确认记录内容（如“以上信息确认无误”）时，用 confirmation_status="confirmed" 并填 confirmation.message_id（那条确认消息的 message_id）。起诉状只能使用已确认的事实。
只记录用户说过或认可的内容：材料里读到的信息先请用户核对，用户认可后按用户陈述记录；不要使用 document_record 或 derived。管辖法院等由你判断得出的结论不是事实，不要入档，在回答里说明并请用户确认。
取值：date 用 "YYYY-MM-DD"；money 用 {"amount":"8000","currency":"CNY"}；其余用 text 原样记录用户说法，不推断、不补全（只说“王某”就记“王某”；只说“10月8日”而无年份时不要猜年份，用 text 记录原话）。
案由明确时（机动车交通事故 traffic_accident、民间借贷 private_lending、买卖合同 sales_contract，其他 other），在同一次 update_case 里用 changes.dispute_type 记下；案件状态里已是同一类型就不重复。起诉状模板按案由选择，案由为空时无法生成。
已有事实内容改变时新增 fact_id，并把旧事实的 superseded_by 设为新 fact_id；不要修改旧事实的取值。保存失败时按返回错误修正一次，仍失败则在回答中说明哪些信息未能保存。
规范字段名：
""" + _fact_field_guide()

AGENT_TEMPLATE = ChatPromptTemplate.from_messages([
    SystemMessage(content=SYSTEM_PROMPT),
    ("system", "{case_context}"),
    ("system", "{stage_instructions}"),
    ("system", RUNTIME_TEMPLATE),
    MessagesPlaceholder("history"),
])

# Include all prompt policy text in the runtime manifest, not just the base role.
PROMPT_SPEC = {
    "system": SYSTEM_PROMPT, "consultation": CONSULTATION_INSTRUCTIONS, "route": ROUTE_INSTRUCTIONS,
    "route_finished": ROUTE_FINISHED, "facts": FACT_RECORDING, "runtime": RUNTIME_TEMPLATE,
}


def stage_instructions(progress: dict | None) -> str:
    """Consultation guidance before a route is chosen, else guidance for the current step."""
    if not progress:
        return CONSULTATION_INSTRUCTIONS + "\n\n" + FACT_RECORDING
    steps = progress["steps"]
    finished = [s for s in steps if s["status"] in ("completed", "not_applicable")]
    completed = ("已完成步骤：" + "；".join(s["title"] + (f"（{s['note']}）" if s.get("note") else "")
                                         for s in finished)) if finished else "已完成步骤：无"
    route_name = ROUTE_NAMES.get(progress["route"], progress["route"])
    current = next((s for s in steps if s["step_id"] == progress["current_step"]), None)
    if current is None:
        text = ROUTE_FINISHED.format(route_name=route_name, completed=completed)
    else:
        outputs = f"\n本步已有成果：{', '.join(current['output_refs'])}（先复核，不要重复生成）" if current.get("output_refs") else ""
        text = ROUTE_INSTRUCTIONS.format(
            route_name=route_name, step_title=current["title"], step_id=current["step_id"],
            step_number=steps.index(current) + 1, step_total=len(steps),
            deliverable=current["deliverable"], materials="、".join(current["materials"]) or "无特别要求",
            condition=current["completion_condition"], tools="、".join(current["tools"]) or "无",
            completed=completed) + outputs
    return text + "\n\n" + FACT_RECORDING


# Bookkeeping the model never needs; route_progress is rendered as stage guidance instead.
_HIDDEN = {"route_progress", "schema_version", "synthetic", "created_at", "updated_at"}
RECENT_STATEMENTS = 6


def _compact(value):
    """Drop empty values so each record shows only what it actually holds."""
    if isinstance(value, dict):
        return {k: c for k, v in value.items() if (c := _compact(v)) not in (None, "", [], {})}
    if isinstance(value, list):
        return [c for v in value if (c := _compact(v)) not in (None, "", [], {})]
    return value


def case_context(snapshot: CaseSnapshot) -> str:
    summary = {k: v for k, v in snapshot.summary.items() if k not in _HIDDEN}
    statements = summary.get("statements") or []
    if len(statements) > RECENT_STATEMENTS:
        summary["statements"] = statements[-RECENT_STATEMENTS:]
        summary["earlier_statements_omitted"] = len(statements) - RECENT_STATEMENTS
    body: dict[str, Any] = {"case_id": snapshot.case_id, "case_version": snapshot.case_version,
                            "stage": snapshot.stage, **_compact(summary)}
    return "当前案件状态（由系统提供，每次调用时更新）：\n" + json.dumps(body, ensure_ascii=False, separators=(",", ":"))


def assemble(history: list[BaseMessage], snapshot: CaseSnapshot,
             max_chars: int | None = None, *, runtime_context: dict | None = None) -> list[BaseMessage]:
    """Render native templates without modifying durable message history."""
    values = {
        "stage_instructions": stage_instructions(snapshot.summary.get("route_progress")),
        "case_context": case_context(snapshot),
        "runtime_context": json.dumps(runtime_context or {}, ensure_ascii=False),
        "history": [],
    }
    head = AGENT_TEMPLATE.invoke(values).to_messages()
    view = history if max_chars is None else compact_history(history, max(max_chars - _size(head), 0))
    return AGENT_TEMPLATE.invoke({**values, "history": view}).to_messages()


KEEP_RECENT_UNITS = 4


def compact_history(history: list[BaseMessage], max_chars: int) -> list[BaseMessage]:
    """Slim older tool results, then drop oldest turns while keeping call/reply pairs."""
    sizes = {}

    def size(message):
        key = id(message)
        if key not in sizes:
            sizes[key] = _size([message])
        return sizes[key]

    if sum(size(message) for message in history) <= max_chars:
        return history
    units = []
    for message in history:
        if isinstance(message, ToolMessage) and units:
            units[-1].append(message)
        else:
            units.append([message])
    first, rest = units[:1], units[1:]
    cut = max(len(rest) - KEEP_RECENT_UNITS, 0)
    older = [[_slim(message) for message in unit] for unit in rest[:cut]]
    recent = rest[cut:]

    def flat(*parts):
        return [message for part in parts for unit in part for message in unit]

    remaining = sum(size(message) for message in flat(first, older, recent))
    dropped = 0
    for unit in older:
        if remaining <= max_chars:
            break
        remaining -= sum(size(message) for message in unit)
        dropped += 1
    out = flat(first, older[dropped:], recent)
    if dropped:
        note = HumanMessage(content=f"（系统提示：为控制长度，较早的 {dropped} 轮对话已省略；已确认的事实见案件状态。）")
        out = flat(first) + [note] + flat(older[dropped:], recent)
    return out


def _slim(message: BaseMessage) -> BaseMessage:
    if not isinstance(message, ToolMessage):
        return message
    try:
        full = json.loads(message.content)
    except (json.JSONDecodeError, TypeError):
        return message
    if not isinstance(full, dict):
        return message
    kept = {key: full[key] for key in ("status", "summary", "error") if key in full}
    kept["note"] = "较早的工具结果，详细数据已省略"
    return message.model_copy(update={"content": json.dumps(kept, ensure_ascii=False)})


def _size(messages: list[BaseMessage]) -> int:
    return sum(len(json.dumps(message_dict(message), ensure_ascii=False)) for message in messages)
