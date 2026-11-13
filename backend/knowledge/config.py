"""Shared retrieval defaults, input variants and artifact paths."""
from dataclasses import dataclass
from pathlib import Path

from backend.common.config import ROOT
CORPUS_DIR = ROOT / "data/processed"
INDEX_DIR = ROOT / "data/index"
INPUT_VARIANTS = ("text", "metadata")
DEFAULT_VARIANT = "metadata"
DEFAULT_MODE = "exact-vector"
DEFAULT_TOP_K = 5
DEFAULT_CANDIDATE_K = 50
DEFAULT_RRF_K = 60

# CLI presets; routing and retrieval algorithms remain separate internally.
MODES = {"keyword": ("keyword", True), "vector": ("vector", False),
         "exact-vector": ("vector", True), "hybrid": ("hybrid", True)}


def includes_metadata(variant: str) -> bool:
    if variant not in INPUT_VARIANTS:
        raise ValueError(f"Unknown input variant: {variant}")
    return variant == "metadata"


def keyword_index_path(variant=DEFAULT_VARIANT, index_dir=INDEX_DIR) -> Path:
    return Path(index_dir) / ("bm25.pkl" if includes_metadata(variant) else "bm25_text.pkl")


def vector_index_path(variant=DEFAULT_VARIANT, index_dir=INDEX_DIR) -> Path:
    includes_metadata(variant)
    return Path(index_dir) / f"vector_{variant}.npz"


@dataclass(frozen=True)
class RetrievalConfig:
    channel: str = "vector"
    exact_match: bool = True
    variant: str = DEFAULT_VARIANT
    top_k: int = DEFAULT_TOP_K
    candidate_k: int = DEFAULT_CANDIDATE_K
    rrf_k: int = DEFAULT_RRF_K

    def __post_init__(self):
        if self.channel not in ("keyword", "vector", "hybrid"):
            raise ValueError(f"Unknown retrieval channel: {self.channel}")
        includes_metadata(self.variant)
        for name in ("top_k", "candidate_k", "rrf_k"):
            if type(getattr(self, name)) is not int or getattr(self, name) < 1:
                raise ValueError(f"{name} must be a positive integer")

    @classmethod
    def from_mode(cls, mode=DEFAULT_MODE, **kwargs):
        if mode not in MODES:
            raise ValueError(f"Unknown retrieval mode: {mode}")
        channel, exact = MODES[mode]
        return cls(channel=channel, exact_match=exact, **kwargs)
