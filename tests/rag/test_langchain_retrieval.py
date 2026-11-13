"""Retrieval component contracts; no network or real keys."""
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import numpy as np
from langchain_core.documents import Document
from langchain_core.embeddings import Embeddings
from langchain_community.retrievers import BM25Retriever

from backend.knowledge.config import RetrievalConfig
from backend.knowledge.corpus import provision_document
from backend.knowledge.keyword.bm25_index import build_index, save_index, load_index
from backend.knowledge.keyword.tokenizer import tokenize
from backend.knowledge.keyword.bm25_retriever import create_bm25_retriever
from backend.knowledge.retrieval.service import RetrievalService
from backend.knowledge.vector.embedding import EmbeddingClient
from backend.knowledge.vector.vector_index import VectorIndex


def provision(number, text):
    return {"provision_id": f"law::{number}", "law_id": "law", "law_title": "中华人民共和国民法典",
            "article_no": number, "hierarchy_path": ["第一章 总则"], "text": text,
            "source_file": "law.pdf", "source_url": "https://example.org/law",
            "page_start": 1, "page_end": 1, "publication_date": "2020-05-28",
            "effective_date": "2021-01-01", "expiry_date": None, "status": "有效"}


class FixedEmbeddings(Embeddings):
    model = "test"
    base_url = "https://example.org/v1"

    def embed_documents(self, texts):
        raise AssertionError("Stored law vectors must not be re-embedded")

    def embed_query(self, text, *, deadline=None):
        return [1., 0.]


class LangChainRetrievalTests(unittest.TestCase):
    def setUp(self):
        self.provisions = [provision("第一条", "定金合同实际交付。"),
                           provision("第二条", "违约损失赔偿。"),
                           provision("第三条", "租赁房屋维修。")]
        self.index = VectorIndex(self.provisions, np.asarray([[1, 0], [0.8, 0.6], [0, 1]], dtype=np.float32),
                                 {"include_metadata": True, "dimensions": 2,
                                  "model": "test", "base_url": FixedEmbeddings.base_url})

    def test_document_preserves_original_quote_and_explicit_index_text(self):
        p = self.provisions[0]
        text = provision_document(p, False)
        metadata = provision_document(p, True)
        self.assertIsInstance(metadata, Document)
        self.assertEqual(text.page_content, p["text"])
        self.assertEqual(metadata.page_content, f"{p['law_title']} 第一章 总则 {p['text']}")
        self.assertEqual(metadata.metadata["text"], p["text"])
        self.assertEqual(metadata.id, p["provision_id"])

    def test_vectorstore_scores_and_concurrent_top_k_do_not_mutate_shared_state(self):
        service = RetrievalService(RetrievalConfig(exact_match=False),
                                   vector_index=self.index, client=FixedEmbeddings())
        self.assertEqual(len(service.vector_retriever.vectorstore.store), 3)
        with ThreadPoolExecutor(max_workers=3) as pool:
            results = list(pool.map(lambda k: service.search("问题", top_k=k), [1, 3, 2]))
        self.assertEqual([len(r["results"]) for r in results], [1, 3, 2])
        result = results[1]["results"]
        self.assertEqual([p["article_no"] for p in result], ["第一条", "第二条", "第三条"])
        self.assertAlmostEqual(result[1]["retrieval"]["score"], 0.8, places=6)
        self.assertEqual(result[0]["text"], self.provisions[0]["text"])
        self.assertEqual(result[0]["source"]["page_start"], 1)
        self.assertEqual(service.vector_retriever.k, 5)
        with self.assertRaises(ValueError):
            service.search("问题", query_vector=[0, 0])

    def test_exact_and_empty_routes_skip_embedding(self):
        embeddings = FixedEmbeddings()
        with patch.object(embeddings, "embed_query", side_effect=AssertionError("unexpected embedding")):
            service = RetrievalService(vector_index=self.index, client=embeddings)
            self.assertEqual(service.search("民法典第一条")["results"][0]["retrieval"]["matched_by"], ["exact"])
            self.assertEqual(service.search("民法典第九千条")["reason"], "exact_article_not_found")
            self.assertEqual(service.search("！？")["reason"], "no_searchable_content")

    def test_embedding_interface_reuses_cache_for_documents_and_queries(self):
        with tempfile.TemporaryDirectory() as tmp:
            client = EmbeddingClient(Path(tmp) / "cache.sqlite3")
            self.assertIsInstance(client, Embeddings)
            with patch.object(client, "_request", return_value=np.array([[0.6, 0.8]], dtype=np.float32)) as request:
                doc_vector = client.embed_documents(["same input"])[0]
                query_vector = client.embed_query("same input")
                self.assertEqual(doc_vector, query_vector)
                self.assertEqual(request.call_count, 1)

    def test_native_bm25_preprocessing_positive_filter_and_pickle_roundtrip(self):
        import json
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "corpus.jsonl"
            path.write_text("\n".join(json.dumps(p, ensure_ascii=False) for p in self.provisions), encoding="utf-8")
            index = build_index([path], variant="text")
            retriever = create_bm25_retriever(index)
            self.assertIsInstance(retriever, BM25Retriever)
            self.assertIs(retriever.preprocess_func, tokenize)
            self.assertEqual(retriever.invoke("zzzxxyyqqq"), [])
            self.assertEqual(retriever.invoke("的"), [])
            service = RetrievalService(RetrievalConfig(channel="hybrid"),
                keyword_index=build_index([path]), vector_index=self.index, client=FixedEmbeddings())
            response = service.search("定金合同", query_vector=[1., 0.])
            self.assertEqual(response["results"][0]["retrieval"]["score_type"], "rrf")
            self.assertEqual(set(response["results"][0]["retrieval"]["channels"]), {"keyword", "vector"})
            output = Path(tmp) / "bm25.pkl"
            save_index(index, output)
            loaded = load_index(output)
            self.assertNotIn("_retriever", loaded.__dict__)
            self.assertEqual(retriever.invoke("定金合同"), create_bm25_retriever(loaded).invoke("定金合同"))

def test_embedding_deadline_bounds_http_timeout_without_mutating_client(tmp_path):
    from datetime import datetime, timedelta, timezone
    from io import BytesIO
    import pytest

    client = EmbeddingClient(tmp_path / "cache.sqlite3", api_key="test", timeout_s=30)
    deadline = datetime.now(timezone.utc) + timedelta(seconds=1)
    with patch("backend.knowledge.vector.embedding.urlopen") as request:
        request.return_value.__enter__.return_value = BytesIO(b'{"data":[{"index":0,"embedding":[1,0]}]}')
        assert client.embed_query("query", deadline=deadline) == [1.0, 0.0]
        assert 0 < request.call_args.kwargs["timeout"] <= 1
        assert client.timeout_s == 30
        with pytest.raises(TimeoutError):
            client.embed_query("another query", deadline=datetime.now(timezone.utc) - timedelta(seconds=1))
        assert request.call_count == 1


if __name__ == "__main__":
    unittest.main()
