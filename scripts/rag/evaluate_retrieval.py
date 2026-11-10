"""Evaluate keyword baselines or all retrieval modes from the repository root."""
import argparse
from datetime import datetime, timedelta, timezone
import hashlib
from importlib.metadata import version
import json
from pathlib import Path
import platform
import sys
from time import perf_counter

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from backend.knowledge.keyword.bm25_index import build_index
from backend.knowledge.retrieval.service import RetrievalService
from backend.knowledge.config import (ROOT, CORPUS_DIR, DATASET_PATH, INDEX_DIR, INPUT_VARIANTS,
    DEFAULT_CANDIDATE_K, DEFAULT_RRF_K, evaluation_specs, includes_metadata, vector_index_path)
from backend.knowledge.keyword.tokenizer import TOKENIZER_VERSION, tokenize


def key(record):
    return record["law_title"], record["article_no"]


def retrieval_metrics(gold, ranked):
    """Per-query metrics; macro-average only queries with nonempty gold."""
    if not gold:
        raise ValueError("Metrics require nonempty gold")
    metrics = {}
    for k in (1, 3, 5):
        found = gold.intersection(ranked[:k])
        metrics[f"hit@{k}"] = float(bool(found))
        metrics[f"recall@{k}"] = len(found) / len(gold)
    metrics["mrr@5"] = next((1 / r for r, p in enumerate(ranked[:5], 1) if p in gold), 0.0)
    return metrics


def average_metrics(rows):
    return {name: sum(row[name] for row in rows) / len(rows) for name in rows[0]} if rows else {}


def grouped_metrics(details, queries):
    article_ids = {q["id"] for q in queries if "exact_article" in q["tags"]}
    groups = {}
    for name in ("non_article", "article"):
        rows = [d["metrics"] for d in details if d["metrics"] is not None
                and ((d["id"] in article_ids) == (name == "article"))]
        groups[name] = {"count": len(rows), "metrics": average_metrics(rows)}
    return groups


def load_queries(path, provisions):
    queries = [json.loads(s) for s in path.read_text(encoding="utf-8-sig").splitlines() if s.strip()]
    if not queries or len({q["id"] for q in queries}) != len(queries) or not any(q["gold"] for q in queries):
        raise ValueError("Dataset needs unique query IDs and supported queries")
    lookup = {key(p): p for p in provisions}
    if len(lookup) != len(provisions):
        raise ValueError("Evaluation gold requires unambiguous law/article keys")
    for q in queries:
        if q["expected_status"] not in ("supported", "insufficient_corpus") or bool(q["gold"]) != (q["expected_status"] == "supported"):
            raise ValueError(f"Invalid status/gold: {q['id']}")
        for g in q["gold"]:
            if key(g) not in lookup or not g["evidence_quote"] or g["evidence_quote"] not in lookup[key(g)]["text"]:
                raise ValueError(f"Invalid gold provision/quote: {q['id']}")
    return queries


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=("keyword", "all"), default="keyword")
    parser.add_argument("--corpus-dir", type=Path, default=CORPUS_DIR)
    parser.add_argument("--dataset", type=Path, default=DATASET_PATH)
    parser.add_argument("--index-dir", type=Path, default=INDEX_DIR)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--candidate-k", type=int, default=DEFAULT_CANDIDATE_K)
    parser.add_argument("--rrf-k", type=int, default=DEFAULT_RRF_K)
    args = parser.parse_args()
    if min(args.candidate_k, args.rrf_k) < 1:
        parser.error("candidate-k and rrf-k must be positive")
    paths = sorted(args.corpus_dir.glob("*.jsonl"))
    bm25 = {"metadata": build_index(paths)}
    queries = load_queries(args.dataset, bm25["metadata"].provisions)
    vectors, indexes, client = [None] * len(queries), {}, None
    report = {
        "schema_version": "1.0", "created_at": datetime.now(timezone(timedelta(hours=8))).isoformat(),
        "python": platform.python_version(),
        "dependencies": {n: version(n) for n in ("rank-bm25", "jieba", "numpy",
                                               "langchain-core", "langchain-community", "pydantic")},
        "retrieval_backend": "langchain",
        "supported_queries": sum(bool(q["gold"]) for q in queries),
        "insufficient_corpus_queries": sum(not q["gold"] for q in queries),
        "metric_notes": "Macro averages on supported queries. Hit: any gold found; Recall: fraction of distinct gold found. Negative empty rate is diagnostic only. All modes share query metrics and actual retrieval code; timings include cache effects.",
        "runs": [],
    }
    specs = evaluation_specs(args.mode, args.candidate_k, args.rrf_k)
    if args.mode == "keyword":
        bm25["text"] = build_index(paths, variant="text")
    else:
        from backend.knowledge.vector.embedding import EmbeddingClient
        from backend.knowledge.vector.vector_index import load_vector_index

        client = EmbeddingClient()
        corpus_hash = hashlib.sha256(json.dumps(bm25["metadata"].provisions, ensure_ascii=False, sort_keys=True).encode()).hexdigest()
        for variant in INPUT_VARIANTS:
            index = load_vector_index(vector_index_path(variant, args.index_dir))
            if index.config["corpus_sha256"] != corpus_hash or index.config["include_metadata"] != includes_metadata(variant):
                parser.error(f"{variant} index corpus/input differs; rebuild it")
            indexes[variant] = index
        started = perf_counter()
        vectors = client.embed([q["query"] for q in queries])
        report.update({"model": client.model, "base_url": client.base_url, "dimensions": vectors.shape[1],
                       "query_embedding_seconds_with_cache": perf_counter() - started})
    inputs = paths + [args.dataset, Path(__file__).resolve(), ROOT / "backend/knowledge/keyword/legal_terms.txt"] + sorted((ROOT / "backend/knowledge").rglob("*.py"))
    report["sha256"] = {p.resolve().relative_to(ROOT).as_posix() if p.resolve().is_relative_to(ROOT) else p.as_posix(): hashlib.sha256(p.read_bytes()).hexdigest() for p in inputs}
    for name, settings in specs:
        channel, variant, exact = settings.channel, settings.variant, settings.exact_match
        service = RetrievalService(settings, keyword_index=bm25.get("metadata" if channel == "hybrid" else variant),
                                   vector_index=indexes.get(variant), client=client)
        details = []
        for q, vector in zip(queries, vectors):
            started = perf_counter()
            hits = service.search(q["query"], query_vector=vector)["results"]
            seconds = perf_counter() - started
            metrics = retrieval_metrics({key(g) for g in q["gold"]}, [key(p) for p in hits]) if q["gold"] else None
            details.append({"id": q["id"], "query": q["query"], "expected_status": q["expected_status"],
                            "gold": q["gold"], "metrics": metrics, "query_seconds": seconds,
                            "results": [{"provision_id": p["provision_id"], "law_title": p["law_title"], "article_no": p["article_no"], "retrieval": p["retrieval"]} for p in hits]})
            if channel == "keyword":
                details[-1]["query_tokens"] = tokenize(q["query"])
        if channel == "keyword":
            index = bm25[variant]
            config = {"include_metadata": index.include_metadata, "exact_match": exact, "tokenizer_version": TOKENIZER_VERSION,
                      "tokenizer_fingerprint": index.tokenizer_fingerprint,
                      "top_k": settings.top_k, "k1": index.bm25.k1, "b": index.bm25.b, "epsilon": index.bm25.epsilon}
        else:
            config = {"vector": indexes[variant].config, "exact_match": exact, "top_k": settings.top_k}
            if channel == "hybrid":
                config.update({"keyword_include_metadata": True, "tokenizer_version": TOKENIZER_VERSION,
                               "tokenizer_fingerprint": bm25["metadata"].tokenizer_fingerprint,
                               "candidate_k": args.candidate_k, "rrf_k": args.rrf_k, "channel_weights": {"keyword": 1, "vector": 1}})
        negative = [q for q in details if q["metrics"] is None]
        summary = average_metrics([q["metrics"] for q in details if q["metrics"] is not None])
        empty = sum(not q["results"] for q in negative)
        report["runs"].append({"name": name, "config": config, "metrics": summary,
                               "groups": grouped_metrics(details, queries), "negative_empty_results": empty,
                               "negative_empty_rate": empty / len(negative) if negative else None, "queries": details})
        print(name, json.dumps(summary), flush=True)
    output = args.output or ROOT / "evaluation/rag" / ("rag_keyword_baseline.json" if args.mode == "keyword" else "rag_vector_baseline.json")
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"Report: {output}")


if __name__ == "__main__":
    main()
