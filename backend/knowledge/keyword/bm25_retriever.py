"""Scored community BM25 retriever with CivRA's unchanged jieba preprocessing."""
from typing import TYPE_CHECKING

from langchain_community.retrievers import BM25Retriever
from langchain_core.callbacks import CallbackManagerForRetrieverRun
from langchain_core.documents import Document

from ..config import DEFAULT_TOP_K
from ..corpus import provision_document
from .tokenizer import tokenize

if TYPE_CHECKING:
    from .bm25_index import BM25Index


def create_bm25_retriever(index: "BM25Index") -> "ScoredBM25Retriever":
    """Adapt an existing index without rebuilding its vectorizer."""
    return ScoredBM25Retriever(
        vectorizer=index.bm25,
        docs=[provision_document(p, index.include_metadata) for p in index.provisions],
        preprocess_func=tokenize, k=DEFAULT_TOP_K)


class ScoredBM25Retriever(BM25Retriever):
    """Add scores/positive filtering without replacing community candidate ranking."""

    def _get_relevant_documents(
        self, query: str, *, run_manager: CallbackManagerForRetrieverRun,
        top_k: int | None = None,
    ) -> list[Document]:
        depth = self.k if top_k is None else top_k
        if type(depth) is not int or depth < 1:
            raise ValueError("top_k must be a positive integer")
        tokens = self.preprocess_func(query)
        if not tokens:
            return []
        # Per-call copy avoids mutating shared k during concurrent invocation.
        configured = self.model_copy(update={"k": depth})
        docs = super(ScoredBM25Retriever, configured)._get_relevant_documents(
            query, run_manager=run_manager)
        scores = dict(zip((doc.id for doc in self.docs), self.vectorizer.get_scores(tokens)))
        return [doc.model_copy(update={"metadata": {
            **doc.metadata, "retrieval": {"score": float(scores[doc.id]), "matched_by": ["keyword"]},
        }}) for doc in docs if scores[doc.id] > 0]
