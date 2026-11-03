"""Dense article retrieval using normalized vectors and exact cosine search."""
from dataclasses import dataclass
import hashlib
import json
from pathlib import Path

import numpy as np

from ..corpus import load_provisions, provision_text


@dataclass
class VectorIndex:
    provisions: list
    vectors: np.ndarray
    config: dict

    def search_vector(self, vector, top_k=8):
        if top_k < 1:
            raise ValueError("top_k must be positive")
        vector = np.asarray(vector, dtype=np.float32)
        if vector.shape != (self.vectors.shape[1],) or not np.isfinite(vector).all() or np.linalg.norm(vector) == 0:
            raise ValueError("Query vector has invalid values or wrong dimensions")
        scores = self.vectors @ (vector / np.linalg.norm(vector))
        ranked = np.argsort(-scores, kind="stable")[:top_k]
        return [(self.provisions[i], float(scores[i])) for i in ranked]


def build_vector_index(paths, client, include_metadata=False, batch_size=16):
    provisions = load_provisions(paths)
    texts = [provision_text(p, include_metadata) for p in provisions]
    vectors = client.embed(texts, batch_size=batch_size)
    config = {
        "index_version": "1.0", "model": client.model, "base_url": client.base_url,
        "include_metadata": include_metadata, "dimensions": vectors.shape[1],
        "query_prefix": "", "similarity": "cosine",
        "corpus_sha256": hashlib.sha256(json.dumps(provisions, ensure_ascii=False, sort_keys=True).encode()).hexdigest(),
        "input_sha256": hashlib.sha256(json.dumps(texts, ensure_ascii=False).encode()).hexdigest(),
    }
    return VectorIndex(provisions, vectors / np.linalg.norm(vectors, axis=1, keepdims=True), config)


def save_vector_index(index, path):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("wb") as stream:
        np.savez_compressed(stream, vectors=index.vectors,
                            metadata=json.dumps({"config": index.config, "provisions": index.provisions}, ensure_ascii=False))
    temporary.replace(path)


def load_vector_index(path):
    with np.load(path, allow_pickle=False) as archive:
        metadata = json.loads(str(archive["metadata"]))
        vectors = archive["vectors"]
    config, provisions = metadata["config"], metadata["provisions"]
    if config["index_version"] != "1.0" or vectors.shape != (len(provisions), config["dimensions"]) or not len(provisions):
        raise ValueError("Invalid vector index; rebuild it")
    if not np.isfinite(vectors).all() or not np.allclose(np.linalg.norm(vectors, axis=1), 1, atol=1e-5):
        raise ValueError("Vector index is not normalized")
    return VectorIndex(provisions, vectors, config)


def search_vector(query, index, client, top_k=8, use_exact=False, query_vector=None):
    """Precomputed query vectors let evaluation exercise the same search path."""
    from ..retrieval.search import _to_result, exact_result

    if top_k < 1:
        raise ValueError("top_k must be positive")
    if not query or not query.strip():
        return {"status": "no_results", "query": query, "results": [], "warnings": ["empty query"]}
    if (client.model, client.base_url) != (index.config["model"], index.config["base_url"]):
        raise ValueError("Query embedding model/endpoint differs from the index")
    if use_exact:
        hit = exact_result(query, index.provisions)
        if hit:
            return {"status": "ok", "query": query, "results": [hit], "warnings": []}
    if query_vector is None:
        query_vector = client.embed([query])[0]
    ranked = index.search_vector(query_vector, top_k)
    return {"status": "ok", "query": query, "results": [
        _to_result(p, rank, score, ["vector"]) for rank, (p, score) in enumerate(ranked, 1)
    ], "warnings": []}
