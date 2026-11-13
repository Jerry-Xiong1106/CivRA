"""Load, validate and describe retrieval resources independently of query routing."""
from collections import Counter
from dataclasses import asdict, dataclass
from typing import Any
import hashlib
import json

from ..config import (
    INDEX_DIR, ROOT, RetrievalConfig, includes_metadata, keyword_index_path, vector_index_path,
)


@dataclass(frozen=True)
class RetrievalResources:
    config: RetrievalConfig
    client: Any
    provisions: list[dict]
    source_metadata: dict
    knowledge_version: str
    knowledge_manifest: dict
    keyword_retriever: Any
    vector_retriever: Any


def prepare_resources(config: RetrievalConfig, *, keyword_index=None, vector_index=None,
                      client=None, source_metadata=None) -> RetrievalResources:
    """Validate injected indexes and prepare reusable retrievers and provenance."""
    needs_keyword = config.channel in ("keyword", "hybrid")
    needs_vector = config.channel in ("vector", "hybrid")
    if needs_keyword:
        if keyword_index is None:
            raise ValueError("This channel requires a keyword index")
        # RRF retains the original metadata BM25 baseline.
        expected = True if config.channel == "hybrid" else includes_metadata(config.variant)
        if keyword_index.include_metadata != expected:
            raise ValueError("Keyword index input variant differs from retrieval config")
    if needs_vector:
        if vector_index is None or client is None:
            raise ValueError("This channel requires a vector index and embedding client")
        if vector_index.config["include_metadata"] != includes_metadata(config.variant):
            raise ValueError("Vector index input variant differs from retrieval config")
        if (client.model, client.base_url) != (vector_index.config["model"], vector_index.config["base_url"]):
            raise ValueError("Query embedding model/endpoint differs from the index")
    if needs_keyword and needs_vector:
        keyword = {p["provision_id"]: p for p in keyword_index.provisions}
        dense = {p["provision_id"]: p for p in vector_index.provisions}
        if len(keyword) != len(keyword_index.provisions) or len(dense) != len(vector_index.provisions) or keyword != dense:
            raise ValueError("BM25 and vector indexes must contain identical provisions and versions; rebuild indexes")
    provisions = vector_index.provisions if needs_vector else keyword_index.provisions
    source_metadata = {
        (s["title"], s["publication_date"], s["local_filename"]): s for s in (source_metadata or [])
    }
    corpus_hash = hashlib.sha256(json.dumps(sorted(provisions, key=lambda p: p["provision_id"]),
        ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()
    knowledge_version = f"sha256:{corpus_hash}"
    knowledge_manifest = {
        "schema_version": "0.1", "knowledge_version": knowledge_version,
        "corpus_sha256": corpus_hash, "provision_count": len(provisions),
        "coverage": dict(sorted(Counter(p["law_title"] for p in provisions).items())),
        "source_types": ["statute"], "case_exclusion_manifest": None,
        "retrieval_config": asdict(config), "vector_index": None, "keyword_index": None,
    }
    if needs_vector:
        knowledge_manifest["vector_index"] = {**vector_index.config,
            "vectors_sha256": hashlib.sha256(vector_index.vectors.tobytes()).hexdigest()}
    if needs_keyword:
        knowledge_manifest["keyword_index"] = {
            "index_version": keyword_index.index_version, "include_metadata": keyword_index.include_metadata,
            "tokenizer_version": keyword_index.tokenizer_version,
            "tokenizer_fingerprint": keyword_index.tokenizer_fingerprint,
            "parameters": {name: getattr(keyword_index.bm25, name) for name in ("k1", "b", "epsilon")},
        }
    keyword_retriever = None
    if needs_keyword:
        from ..keyword.bm25_retriever import create_bm25_retriever
        keyword_retriever = create_bm25_retriever(keyword_index)
    vector_retriever = None
    if needs_vector:
        from ..vector.vector_retriever import create_vector_retriever
        vector_retriever = create_vector_retriever(vector_index, client, config.top_k)

    return RetrievalResources(config, client, provisions,
                              source_metadata, knowledge_version, knowledge_manifest,
                              keyword_retriever, vector_retriever)


def load_resources(config: RetrievalConfig | None = None, *, index_dir=INDEX_DIR,
                   keyword_path=None, vector_path=None) -> RetrievalResources:
    """Load only the selected channel; environment loading belongs to entry points."""
    config = config or RetrievalConfig()
    keyword, vector, client = None, None, None
    if config.channel in ("keyword", "hybrid"):
        from ..keyword.bm25_index import load_index
        variant = "metadata" if config.channel == "hybrid" else config.variant
        keyword = load_index(keyword_path or keyword_index_path(variant, index_dir))
    if config.channel in ("vector", "hybrid"):
        from ..vector.vector_index import load_vector_index
        from ..vector.embedding import EmbeddingClient
        vector = load_vector_index(vector_path or vector_index_path(config.variant, index_dir))
        client = EmbeddingClient.from_env()
    metadata_path = ROOT / "data/metadata/legal_sources.json"
    metadata = json.loads(metadata_path.read_text(encoding="utf-8-sig")) if metadata_path.exists() else []
    return prepare_resources(config, keyword_index=keyword, vector_index=vector, client=client, source_metadata=metadata)
