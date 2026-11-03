"""Chinese tokenizer for BM25 indexing, backed by jieba with a legal term dictionary."""

from __future__ import annotations

import re
from pathlib import Path

import jieba

LEGAL_TERMS_PATH = Path(__file__).parent / "legal_terms.txt"
STOPWORDS = {"的", "了", "和", "与", "及", "或", "之", "其", "并", "等", "对", "为", "在", "有", "是"}
_TOKEN_RE = re.compile(r"[㐀-鿿]+|[A-Za-z0-9]+")

_jieba = jieba.Tokenizer()
_jieba.load_userdict(str(LEGAL_TERMS_PATH))

TOKENIZER_VERSION = "jieba_baseline_v1"


def tokenize(text: str) -> list[str]:
    """Segment Chinese legal text into tokens, dropping punctuation and stopwords."""
    tokens = []
    for word in _jieba.lcut(text):
        word = word.strip()
        if len(word) < 2 and not word.isdigit():
            continue
        if word in STOPWORDS or not _TOKEN_RE.fullmatch(word):
            continue
        tokens.append(word)
    return tokens
