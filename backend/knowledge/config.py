"""Shared retrieval defaults, input variants, artifact paths and baseline plans."""
from dataclasses import dataclass, replace
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
CORPUS_DIR = ROOT / "data/processed"
INDEX_DIR = ROOT / "data/index"
DATASET_PATH = ROOT / "evaluation/rag/rag_retrieval_dev.jsonl"
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


def evaluation_specs(mode: str, candidate_k=DEFAULT_CANDIDATE_K, rrf_k=DEFAULT_RRF_K):
    """Existing three/ten baselines. RRF always uses metadata BM25."""
    if mode not in ("keyword", "all"):
        raise ValueError(f"Unknown evaluation mode: {mode}")
    base = RetrievalConfig(channel="keyword", exact_match=False,
                           candidate_k=candidate_k, rrf_k=rrf_k)
    specs = [("bm25_metadata", base), ("exact_bm25_metadata", replace(base, exact_match=True))]
    if mode == "keyword":
        return [("bm25_text", replace(base, variant="text"))] + specs
    for channel, prefix, exact in (("vector", "vector", False), ("vector", "exact_vector", True),
                                   ("hybrid", "rrf", False), ("hybrid", "exact_rrf", True)):
        specs.extend((f"{prefix}_{variant}", replace(base, channel=channel, variant=variant, exact_match=exact))
                     for variant in INPUT_VARIANTS)
    return specs
