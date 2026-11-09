"""Chinese tokenizer for BM25 indexing, backed by jieba with a legal term dictionary."""

from __future__ import annotations

import re
import hashlib
import io
import json
from pathlib import Path

import jieba

LEGAL_TERMS_PATH = Path(__file__).parent / "legal_terms.txt"
STOPWORDS = {"的", "了", "和", "与", "及", "或", "之", "其", "并", "等", "对", "为", "在", "有", "是"}
_TOKEN_RE = re.compile(r"[㐀-鿿]+|[A-Za-z0-9]+")

_LEGAL_TERMS_BYTES = LEGAL_TERMS_PATH.read_bytes()
_SOURCE_SHA256 = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
_jieba = jieba.Tokenizer()
_jieba.load_userdict(io.BytesIO(_LEGAL_TERMS_BYTES))

TOKENIZER_VERSION = "jieba_baseline_v1"


def get_tokenizer_fingerprint() -> str:
    """Fingerprint the loaded tokenizer, rejecting edits pending a restart."""
    if (LEGAL_TERMS_PATH.read_bytes() != _LEGAL_TERMS_BYTES or
            hashlib.sha256(Path(__file__).read_bytes()).hexdigest() != _SOURCE_SHA256):
        raise ValueError("Tokenizer files changed after initialization; restart the process before building/loading a BM25 index")
    config = {
        "tokenizer_version": TOKENIZER_VERSION,
        "jieba_version": jieba.__version__,
        "legal_terms_sha256": hashlib.sha256(_LEGAL_TERMS_BYTES).hexdigest(),
        "source_sha256": _SOURCE_SHA256,
        "stopwords": sorted(STOPWORDS),
        "token_pattern": _TOKEN_RE.pattern,
        "token_flags": _TOKEN_RE.flags,
    }
    return hashlib.sha256(json.dumps(config, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()


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
