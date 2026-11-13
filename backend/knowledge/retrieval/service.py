"""Public query routing over validated, reusable retrieval resources."""
from ..config import INDEX_DIR, RetrievalConfig
from ..exact.router import ExactRouter
from .resources import RetrievalResources, load_resources, prepare_resources
from .results import no_results, query_preflight, to_result


class RetrievalService:
    def __init__(self, config: RetrievalConfig | None = None, *, keyword_index=None,
                 vector_index=None, client=None, source_metadata=None,
                 resources: RetrievalResources | None = None):
        """Accept either prepared resources or indexes supplied by an existing caller."""
        if resources is not None and any(item is not None for item in
                (config, keyword_index, vector_index, client, source_metadata)):
            raise ValueError("Pass resources or individual resource inputs, not both")
        resources = resources or prepare_resources(
            config or RetrievalConfig(), keyword_index=keyword_index, vector_index=vector_index,
            client=client, source_metadata=source_metadata)
        self.config = resources.config
        self.client, self.provisions = resources.client, resources.provisions
        self.source_metadata = resources.source_metadata
        self.knowledge_version, self.knowledge_manifest = resources.knowledge_version, resources.knowledge_manifest
        self.keyword_retriever, self.vector_retriever = resources.keyword_retriever, resources.vector_retriever
        self.exact_router = ExactRouter(self.provisions) if self.config.exact_match else None

    @classmethod
    def from_config(cls, config: RetrievalConfig | None = None, *, index_dir=INDEX_DIR,
                    keyword_path=None, vector_path=None):
        return cls(resources=load_resources(config, index_dir=index_dir,
                                            keyword_path=keyword_path, vector_path=vector_path))

    def search(self, query: str, top_k: int | None = None, *, query_vector=None, deadline=None) -> dict:
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
            documents = self.keyword_retriever.invoke(query, top_k=top_k)
            if not documents:
                return no_results(query, "no_keyword_matches", "当前关键词索引没有正分候选；这不表示语料中没有相关法律依据。")
        else:
            if query_vector is None:
                query_vector = self.client.embed_query(query, **({"deadline": deadline} if deadline is not None else {}))
            if self.config.channel == "vector":
                documents = self.vector_retriever.invoke(query, top_k=top_k, query_vector=query_vector)
            else:
                from .fusion import search_hybrid
                results = search_hybrid(query, self.keyword_retriever, self.vector_retriever, query_vector,
                                        top_k, self.config.candidate_k, self.config.rrf_k)
        if self.config.channel != "hybrid":
            results = [to_result(doc.metadata, rank, doc.metadata["retrieval"]["score"], [self.config.channel])
                       for rank, doc in enumerate(documents, 1)]
        if not results:
            return no_results(query, "no_candidates", "当前检索通道没有返回候选。")
        return {"status": "ok", "query": query, "results": results, "warnings": []}
