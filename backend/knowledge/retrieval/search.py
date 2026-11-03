"""Public Python retrieval service shared by CLI, evaluation and future tools."""
from ..config import INDEX_DIR, RetrievalConfig, includes_metadata, keyword_index_path, vector_index_path
from ..exact.router import ExactRouter
from .results import no_results, query_preflight, to_result


class RetrievalService:
    def __init__(self, config: RetrievalConfig | None = None, *, keyword_index=None,
                 vector_index=None, client=None):
        """Inject loaded resources; use from_config for local artifact loading."""
        self.config = config or RetrievalConfig()
        self.keyword_index, self.vector_index, self.client = keyword_index, vector_index, client
        needs_keyword = self.config.channel in ("keyword", "hybrid")
        needs_vector = self.config.channel in ("vector", "hybrid")
        if needs_keyword:
            if keyword_index is None:
                raise ValueError("This channel requires a keyword index")
            # RRF retains the original metadata BM25 baseline.
            expected = True if self.config.channel == "hybrid" else includes_metadata(self.config.variant)
            if keyword_index.include_metadata != expected:
                raise ValueError("Keyword index input variant differs from retrieval config")
        if needs_vector:
            if vector_index is None or client is None:
                raise ValueError("This channel requires a vector index and embedding client")
            if vector_index.config["include_metadata"] != includes_metadata(self.config.variant):
                raise ValueError("Vector index input variant differs from retrieval config")
            if (client.model, client.base_url) != (vector_index.config["model"], vector_index.config["base_url"]):
                raise ValueError("Query embedding model/endpoint differs from the index")
        if needs_keyword and needs_vector:
            keyword = {p["provision_id"]: p for p in keyword_index.provisions}
            dense = {p["provision_id"]: p for p in vector_index.provisions}
            if len(keyword) != len(keyword_index.provisions) or len(dense) != len(vector_index.provisions) or keyword != dense:
                raise ValueError("BM25 and vector indexes must contain identical provisions and versions; rebuild indexes")
        self.provisions = vector_index.provisions if needs_vector else keyword_index.provisions
        self.exact_router = ExactRouter(self.provisions) if self.config.exact_match else None

    @classmethod
    def from_config(cls, config: RetrievalConfig | None = None, *, index_dir=INDEX_DIR,
                    keyword_path=None, vector_path=None):
        """Load only the selected channel's dependencies once, then reuse search."""
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
            client = EmbeddingClient()
        return cls(config, keyword_index=keyword, vector_index=vector, client=client)

    def search(self, query: str, top_k: int | None = None, *, query_vector=None) -> dict:
        """ok means candidates exist, not that they suffice to answer the query.

        query_vector is an evaluation hook; ordinary callers leave it unset.
        """
        top_k = self.config.top_k if top_k is None else top_k
        if type(top_k) is not int or top_k < 1:
            raise ValueError("top_k must be a positive integer")
        response = query_preflight(query)
        if response is not None:
            return response
        if self.exact_router is not None:
            response = self.exact_router.search(query)
            if response is not None:
                return response
        if self.config.channel == "keyword":
            from ..keyword.tokenizer import tokenize
            if not tokenize(query):
                return no_results(query, "no_keyword_tokens", "按当前原始分词规则未产生可检索词；可补充具体表述或尝试向量检索。")
            ranked = self.keyword_index.search(query, top_k)
            if not ranked:
                return no_results(query, "no_keyword_matches", "当前关键词索引没有正分候选；这不表示语料中没有相关法律依据。")
            results = [to_result(p, rank, score, ["keyword"]) for rank, (p, score) in enumerate(ranked, 1)]
        else:
            if query_vector is None:
                query_vector = self.client.embed([query])[0]
            if self.config.channel == "vector":
                ranked = self.vector_index.search_vector(query_vector, top_k)
                results = [to_result(p, rank, score, ["vector"]) for rank, (p, score) in enumerate(ranked, 1)]
            else:
                from .hybrid_search import search_hybrid
                results = search_hybrid(query, self.keyword_index, self.vector_index, query_vector,
                                        top_k, self.config.candidate_k, self.config.rrf_k)
        if not results:
            return no_results(query, "no_candidates", "当前检索通道没有返回候选。")
        return {"status": "ok", "query": query, "results": results, "warnings": []}
