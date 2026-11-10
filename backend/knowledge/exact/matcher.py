"""Recognize explicit provision lists without silently dropping references."""

from __future__ import annotations

import re

LAW_ALIASES = {
    "道路交通安全法": "中华人民共和国道路交通安全法",
    "交通安全法": "中华人民共和国道路交通安全法",
    "民法典": "中华人民共和国民法典",
    "民事诉讼法": "中华人民共和国民事诉讼法",
    "民诉法": "中华人民共和国民事诉讼法",
}

_NAMES = {**LAW_ALIASES, **{title: title for title in LAW_ALIASES.values()}}
_NUMBERS = "一二三四五六七八九十百千万零〇0-9"
_ARTICLE_TOKEN = rf"第[{_NUMBERS}]+条(?:之[{_NUMBERS}]+)?"
ARTICLE_RE = re.compile(_ARTICLE_TOKEN)
LAW_RE = re.compile("|".join(re.escape(name) for name in sorted(_NAMES, key=len, reverse=True)))
_LAW_TOKEN = rf"[《〈]?(?:{LAW_RE.pattern})[》〉]?"
_SEPARATOR = r"[ \t·]*"
_ARTICLES = rf"{_ARTICLE_TOKEN}(?:\s*(?:和|与|及|以及|、|，|,)\s*{_ARTICLE_TOKEN})*"
EXACT_QUERY_RE = re.compile(
    rf"(?P<law1>{_LAW_TOKEN}){_SEPARATOR}(?P<articles1>{_ARTICLES})"
    rf"|(?P<articles2>{_ARTICLES}){_SEPARATOR}(?P<law2>{_LAW_TOKEN})"
)


class UnsupportedExactQuery(ValueError):
    """Explicit references exist but cannot all be assigned unambiguously."""


def normalize_article(article: str) -> str:
    """Normalize Arabic article numbers; preserve Chinese corpus spelling."""
    if "之" in article:
        raise UnsupportedExactQuery("当前不支持条号中的“之一”等附加编号，请分开说明检索目标。")
    number = article[1:-1]
    if not number.isascii() or not number.isdigit():
        if any(char.isdigit() for char in number):
            raise UnsupportedExactQuery("条号不能混用中文和阿拉伯数字，请使用完整条号。")
        return article
    # Existing laws have fewer than 10,000 articles. Larger numbers cannot
    # exist in this corpus; leave them explicit for exact_article_not_found.
    digits = number.lstrip("0") or "0"
    if len(digits) > 4:
        return f"第{digits}条"
    value = int(digits)
    if value == 0:
        return "第零条"
    parts = []
    zero = False
    for unit, label in ((1000, "千"), (100, "百"), (10, "十"), (1, "")):
        digit, value = divmod(value, unit)
        if digit:
            if zero:
                parts.append("零")
            if not (unit == 10 and digit == 1 and not parts):
                parts.append("零一二三四五六七八九"[digit])
            parts.append(label)
            zero = False
        elif parts and value:
            zero = True
    return f"第{''.join(parts)}条"


def match_exact_query(query: str) -> list[tuple[str, str]] | None:
    """Return ordered distinct targets, None for semantic queries, or reject.

    Lists require complete article tokens. Ranges and ambiguous shared law/
    article references are rejected rather than returning a partial match.
    """
    laws = list(LAW_RE.finditer(query))
    articles = list(ARTICLE_RE.finditer(query))
    if not laws:
        return None
    if not articles:
        if re.search(rf"第[{_NUMBERS}]+[、，,至到\-—~～]", query) and "条" in query:
            raise UnsupportedExactQuery("当前不支持条号范围或省略写法，请逐条写明法律名称和完整条号。")
        return None
    if re.search(rf"{_ARTICLE_TOKEN}\s*(?:至|到|[-—~～])\s*{_ARTICLE_TOKEN}", query):
        raise UnsupportedExactQuery("当前不支持条号范围，请逐条列出需要查询的完整条号。")
    matches = list(EXACT_QUERY_RE.finditer(query))
    # Every named law and article must belong to a recognized group. Do not
    # inherit a law across prose or guess which law an unbound article means.
    if not matches or any(not any(m.start() <= token.start() and token.end() <= m.end()
                                  for m in matches) for token in laws + articles):
        raise UnsupportedExactQuery("无法完整确定法律与条号的对应关系，请逐条写明“法律名称＋完整条号”；暂不支持条号范围。")
    if re.search(rf"(?:和|与|及|、|，|,|至|到|[-—~～])\s*[{_NUMBERS}]+条", query):
        raise UnsupportedExactQuery("当前不支持省略“第”的条号列表，请使用“第一条和第二条”等完整写法。")
    targets = []
    for match in matches:
        law = match.group("law1") or match.group("law2")
        title = _NAMES[law.strip("《〈》〉")]
        for article in ARTICLE_RE.findall(match.group("articles1") or match.group("articles2")):
            target = title, normalize_article(article)
            if target not in targets:
                targets.append(target)
    return targets
