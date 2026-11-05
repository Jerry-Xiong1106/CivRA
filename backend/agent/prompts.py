"""System prompt and per-call context assembly."""

from __future__ import annotations

import json
from typing import Any

from backend.agent.case_port import CaseSnapshot

SYSTEM_PROMPT = """你是 CivRA，一个面向中国内地普通用户的民事法律辅助助手。

工作方式：
1. 先弄清案情。关键事实缺失或材料冲突时，用 ask_user 提出具体问题，不要自行补造身份、日期、金额或事故细节。
2. 需要法律依据或类案时调用检索工具，只引用工具返回的来源；检索不到时如实说明。
3. 工具结果中的 status：ok 表示可用；empty 表示没找到，可改写查询再试；insufficient 表示信息不足，考虑追问或标注缺口；error 表示失败，可换方法或说明无法完成。
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


def assemble(history: list[dict[str, Any]], snapshot: CaseSnapshot, max_chars: int | None = None) -> list[dict[str, Any]]:
    """System prompt + fresh case state + conversation history.

    Case state is rebuilt on every call instead of stored in history, so the
    model always sees the latest case_version. The stored history is never
    modified; only this view is compacted when it grows past max_chars.
    """
    head = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "system", "content": case_context(snapshot)},
    ]
    if max_chars is not None:
        history = compact_history(history, max(max_chars - _size(head), 0))
    return [*head, *history]


KEEP_RECENT_UNITS = 4  # most recent turns are always sent in full


def compact_history(history: list[dict[str, Any]], max_chars: int) -> list[dict[str, Any]]:
    """Shrink older history to fit max_chars, in two passes.

    1. Older tool results keep only status / summary / error (data and full
       excerpts dropped). Confirmed facts are already in the case state.
    2. If still too long, drop the oldest turns after the first user message.

    A turn is a user message, or an assistant message together with its tool
    replies, so every tool_call keeps its matching tool message.
    """
    if _size(history) <= max_chars:
        return history
    units = _units(history)
    first, rest = units[:1], units[1:]
    cut = max(len(rest) - KEEP_RECENT_UNITS, 0)
    older = [[_slim(m) for m in u] for u in rest[:cut]]
    recent = rest[cut:]

    def flat(*parts: list[list[dict]]) -> list[dict]:
        return [m for part in parts for u in part for m in u]

    out = flat(first, older, recent)
    dropped = 0
    while _size(out) > max_chars and older:
        older.pop(0)
        dropped += 1
        out = flat(first, older, recent)
    if dropped:
        note = {"role": "user", "content": f"（系统提示：为控制长度，较早的 {dropped} 轮对话已省略；已确认的事实见案件状态。）"}
        out = flat(first) + [note] + flat(older, recent)
    return out


def _units(history: list[dict[str, Any]]) -> list[list[dict[str, Any]]]:
    units: list[list[dict[str, Any]]] = []
    for m in history:
        if m["role"] == "tool" and units:
            units[-1].append(m)
        else:
            units.append([m])
    return units


def _slim(m: dict[str, Any]) -> dict[str, Any]:
    if m["role"] != "tool":
        return m
    try:
        full = json.loads(m["content"])
    except (json.JSONDecodeError, TypeError):
        return m
    kept = {k: full[k] for k in ("status", "summary", "error") if k in full}
    kept["note"] = "较早的工具结果，详细数据已省略"
    return {**m, "content": json.dumps(kept, ensure_ascii=False)}


def _size(messages: list[dict[str, Any]]) -> int:
    return sum(len(json.dumps(m, ensure_ascii=False)) for m in messages)
