"""Scored LangChain retriever backed by InMemoryVectorStore."""
from typing import TYPE_CHECKING

import numpy as np
from langchain_core.callbacks import CallbackManagerForRetrieverRun
from langchain_core.documents import Document
from langchain_core.retrievers import BaseRetriever
from langchain_core.vectorstores import InMemoryVectorStore
from langchain_core.embeddings import Embeddings

from ..config import DEFAULT_TOP_K
from ..corpus import provision_document

if TYPE_CHECKING:
    from .vector_index import VectorIndex


def create_vector_retriever(
    index: "VectorIndex", embeddings: Embeddings, top_k=DEFAULT_TOP_K,
) -> "ScoredVectorRetriever":
    """Load existing vectors into LangChain without re-embedding laws.

    InMemoryVectorStore.load() assigns the same public store mapping. This
    framework boundary is version-pinned and tested, with no private APIs.
    """
    store = InMemoryVectorStore(embeddings)
    documents = [provision_document(p, index.config["include_metadata"]) for p in index.provisions]
    store.store = {
        doc.id: {"id": doc.id, "text": doc.page_content, "metadata": doc.metadata,
                 "vector": vector.tolist()}
        for doc, vector in zip(documents, index.vectors, strict=True)
    }
    return ScoredVectorRetriever(vectorstore=store, dimensions=index.config["dimensions"], k=top_k)


class ScoredVectorRetriever(BaseRetriever):
    vectorstore: InMemoryVectorStore
    dimensions: int
    k: int = DEFAULT_TOP_K

    def _get_relevant_documents(
        self, query: str, *, run_manager: CallbackManagerForRetrieverRun,
        top_k: int | None = None, query_vector=None,
    ) -> list[Document]:
        depth = self.k if top_k is None else top_k
        if type(depth) is not int or depth < 1:
            raise ValueError("top_k must be a positive integer")
        if query_vector is None:
            query_vector = self.vectorstore.embeddings.embed_query(query)
        vector = np.asarray(query_vector, dtype=np.float32)
        if vector.shape != (self.dimensions,) or not np.isfinite(vector).all() or np.linalg.norm(vector) == 0:
            raise ValueError("Query vector has invalid values or wrong dimensions")
        ranked = self.vectorstore.similarity_search_with_score_by_vector(vector.tolist(), k=depth)
        return [doc.model_copy(update={"metadata": {
            **doc.metadata, "retrieval": {"score": float(score), "matched_by": ["vector"]},
        }}) for doc, score in ranked]
