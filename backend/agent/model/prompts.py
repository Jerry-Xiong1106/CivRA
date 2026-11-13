"""System prompt and per-call context assembly."""

from __future__ import annotations

import json
from typing import Any

from langchain_core.messages import BaseMessage, HumanMessage, SystemMessage, ToolMessage

from backend.agent.case_port import CaseSnapshot
from backend.agent.model.llm import message_dict

SYSTEM_PROMPT = """你是 CivRA，一个面向中国内地普通用户的民事法律辅助助手。

工作方式：
1. 先弄清案情。关键事实缺失或材料冲突时，用 ask_user 提出具体问题，不要自行补造身份、日期、金额或事故细节。
2. 需要法律依据或类案时调用检索工具，只引用工具返回的来源；检索不到时如实说明。
3. 工具结果中的 status：ok 表示正常返回，不等于候选已提供充分依据；empty 表示没找到，可改写查询再试；insufficient 表示信息不足，考虑追问或标注缺口；error 表示失败，可换方法或说明无法完成。
4. 用户确认的新事实用 update_case 写入案件记录。
5. 完成咨询后，如用户需要进一步处理，用 request_route_choice 让用户选择协商和解、调解或诉讼。这些路线都是可选的，不要写成必经程序。
6. 分析可能结果时说明事实前提、支持与不利因素，不给出胜诉概率。
7. 完成后调用 finish，列出仍未解决的事项。

你的输出用于辅助用户准备材料和了解情况，不替代律师意见。"""


def case_context(snapshot: CaseSnapshot) -> str:
    body: dict[str, Any] = {
        "case_id": snapshot.case_id,
        "case_version": snapshot.case_version,
        "stage": snapshot.stage,
        **snapshot.summary,
    }
    return "当前案件状态（由系统提供，每次调用时更新）：\n" + json.dumps(body, ensure_ascii=False, indent=2)


def assemble(history: list[BaseMessage], snapshot: CaseSnapshot,
             max_chars: int | None = None) -> list[BaseMessage]:
    """Build only the model view, preserving native messages and durable history."""
    head = [SystemMessage(content=SYSTEM_PROMPT), SystemMessage(content=case_context(snapshot))]
    view = history if max_chars is None else compact_history(history, max(max_chars - _size(head), 0))
    return [*head, *view]


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
