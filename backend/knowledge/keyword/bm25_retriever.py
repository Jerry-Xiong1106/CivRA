"""Scored community BM25 retriever with CivRA's unchanged jieba preprocessing."""
from typing import TYPE_CHECKING

import numpy as np

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
    """Score once, preserving rank_bm25's get_top_n ordering and positive filter."""

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
        assert self.vectorizer.corpus_size == len(self.docs), "The documents given don't match the index corpus!"
        scores = self.vectorizer.get_scores(tokens)
        # Match rank_bm25.get_top_n exactly, including NumPy's default ties.
        # Keep depth local so concurrent calls never mutate the shared k.
        ranked = np.argsort(scores)[::-1][:depth]
        return [self.docs[i].model_copy(update={"metadata": {
            **self.docs[i].metadata, "retrieval": {"score": float(scores[i]), "matched_by": ["keyword"]},
        }}) for i in ranked if scores[i] > 0]
