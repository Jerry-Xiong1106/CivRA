"""Recognize existing law aliases and article tokens without rewriting queries."""

from __future__ import annotations

import re

LAW_ALIASES = {
    "道路交通安全法": "中华人民共和国道路交通安全法",
    "交通安全法": "中华人民共和国道路交通安全法",
    "民法典": "中华人民共和国民法典",
    "民事诉讼法": "中华人民共和国民事诉讼法",
    "民诉法": "中华人民共和国民事诉讼法",
}

_ARTICLE_TOKEN = r"第[一二三四五六七八九十百千万零〇0-9]+条"
_LAW_NAME = "|".join(re.escape(name) for name in sorted(LAW_ALIASES, key=len, reverse=True))
# Accept either order: law name before or after the article number, with
# optional book quotes and separators such as a middle dot.
EXACT_QUERY_RE = re.compile(
    rf"(?:[《〈]?(?P<law1>{_LAW_NAME})[》〉]?[ \t·]*(?P<article1>{_ARTICLE_TOKEN}))"
    rf"|(?:(?P<article2>{_ARTICLE_TOKEN})[ \t·]*[《〈]?(?P<law2>{_LAW_NAME})[》〉]?)"
)


def match_exact_query(query: str) -> tuple[str, str] | None:
    """Return (law_title, article_no) if the query names a specific provision."""
    match = EXACT_QUERY_RE.search(query)
    if not match:
        return None
    law = match.group("law1") or match.group("law2")
    article = match.group("article1") or match.group("article2")
    return LAW_ALIASES[law], article
