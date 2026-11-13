"""Evaluation plans translated into runtime and retrieval settings."""
from dataclasses import replace
from typing import Any

from backend.common.config import ROOT
from backend.common.schemas import RunConfig
from backend.knowledge.config import RetrievalConfig, DEFAULT_CANDIDATE_K, DEFAULT_RRF_K, INPUT_VARIANTS

DATASET_PATH = ROOT / "evaluation/rag/rag_retrieval_dev.jsonl"


def run_config_from_pilot(plan: dict[str, Any]) -> RunConfig:
    """Map member 5's pilot plan (evaluation/configs/*.json) onto RunConfig.

    Model retries are 0 because the client never retries; tool retries come
    from each ToolSpec (default 1), matching the pilot's tool_retries=1.
    """
    budgets = plan.get("budgets", {})
    if budgets.get("model_retries", 0) != 0 or budgets.get("tool_retries", 1) != 1:
        raise ValueError("当前策略为model_retries=0、tool_retries=1；不支持的预算不能被忽略。")
    return RunConfig(
        mode="agent",
        model=plan.get("model"),
        temperature=plan.get("temperature", 0.0),
        seed=plan.get("seed"),
        max_output_tokens=plan.get("max_output_tokens"),
        max_model_calls=budgets.get("max_model_calls"),
        max_tool_calls=budgets.get("max_tool_calls", RunConfig().max_tool_calls),
        max_total_tokens=budgets.get("max_total_tokens"),
        max_run_seconds=budgets.get("max_active_seconds"),
    )


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
