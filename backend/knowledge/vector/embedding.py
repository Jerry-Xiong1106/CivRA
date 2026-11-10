"""SiliconFlow dense embeddings with a persistent model-specific cache."""
import hashlib
from contextlib import closing
import json
import math
import os
from pathlib import Path
import sqlite3
import sys
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

import numpy as np
from langchain_core.embeddings import Embeddings

from ..config import ROOT, INDEX_DIR


def load_local_env():
    path = ROOT / ".env"
    if path.exists():
        for line in path.read_text(encoding="utf-8-sig").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                name, value = line.split("=", 1)
                os.environ.setdefault(name.strip(), value.strip().strip("\"'"))


class EmbeddingClient(Embeddings):
    def __init__(self, cache_path=None, *, timeout_s=None):
        load_local_env()
        self.timeout_s = float(timeout_s if timeout_s is not None else
                               os.getenv("RAG_EMBEDDING_TIMEOUT_S", "30"))
        if not math.isfinite(self.timeout_s) or self.timeout_s <= 0:
            raise ValueError("Embedding timeout_s must be finite and positive")
        self.model = os.getenv("RAG_EMBEDDING_MODEL", "BAAI/bge-m3")
        self.base_url = os.getenv("RAG_EMBEDDING_BASE_URL", "https://api.siliconflow.cn/v1").rstrip("/")
        if not self.base_url.startswith("https://"):
            raise ValueError("Embedding endpoint must use HTTPS")
        self.api_key = os.getenv("SILICONFLOW_API_KEY", "")
        self.cache_path = Path(cache_path or INDEX_DIR / "embedding_cache.sqlite3")
        self.cache_path.parent.mkdir(parents=True, exist_ok=True)
        with closing(sqlite3.connect(self.cache_path)) as db, db:
            db.execute("CREATE TABLE IF NOT EXISTS embeddings (key TEXT PRIMARY KEY, vector TEXT NOT NULL)")

    def _key(self, text):
        return hashlib.sha256(json.dumps([self.base_url, self.model, text], ensure_ascii=False).encode()).hexdigest()

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        """LangChain interface; share the existing model-specific SQLite cache."""
        return self.embed(texts).tolist()

    def embed_query(self, text: str) -> list[float]:
        return self.embed([text])[0].tolist()

    def _request(self, texts):
        """One HTTP attempt; the calling Runtime owns retries."""
        if not self.api_key:
            raise ValueError("Set SILICONFLOW_API_KEY in the environment or local .env")
        payload = json.dumps({"model": self.model, "input": texts}).encode("utf-8")
        request = Request(self.base_url + "/embeddings", data=payload, headers={
            "Authorization": "Bearer " + self.api_key, "Content-Type": "application/json",
        })
        try:
            with urlopen(request, timeout=self.timeout_s) as response:
                body = json.load(response)
        except HTTPError as exc:
            status = exc.code
            exc.close()
            raise RuntimeError(f"Embedding API returned HTTP {status}; check model, quota and credentials") from None
        except TimeoutError:
            raise TimeoutError("Embedding API request timed out") from None
        except URLError as exc:
            if isinstance(exc.reason, TimeoutError):
                raise TimeoutError("Embedding API request timed out") from None
            raise RuntimeError("Embedding API connection failed") from None
        items = sorted(body["data"], key=lambda item: item["index"])
        if [item["index"] for item in items] != list(range(len(texts))):
            raise ValueError("Embedding response has missing/duplicate indices")
        vectors = np.asarray([item["embedding"] for item in items], dtype=np.float32)
        if vectors.ndim != 2 or vectors.shape[1] == 0 or not np.isfinite(vectors).all() or (np.linalg.norm(vectors, axis=1) == 0).any():
            raise ValueError("Embedding response contains invalid vectors")
        return vectors

    def embed(self, texts, batch_size=16):
        if batch_size < 1 or not texts or any(not isinstance(t, str) or not t.strip() for t in texts):
            raise ValueError("Inputs must be nonempty strings and batch_size positive")
        keys = [self._key(text) for text in texts]
        cached = {}
        with closing(sqlite3.connect(self.cache_path)) as db, db:
            for key in set(keys):
                row = db.execute("SELECT vector FROM embeddings WHERE key=?", (key,)).fetchone()
                if row:
                    cached[key] = json.loads(row[0])
            pending = list(dict.fromkeys(key for key in keys if key not in cached))
            by_key = dict(zip(keys, texts))
            for start in range(0, len(pending), batch_size):
                batch = pending[start:start + batch_size]
                vectors = self._request([by_key[key] for key in batch])
                for key, vector in zip(batch, vectors):
                    cached[key] = vector.tolist()
                    db.execute("INSERT OR REPLACE INTO embeddings VALUES (?, ?)", (key, json.dumps(cached[key])))
                db.commit()
                print(f"Embedding: {min(start + batch_size, len(pending))}/{len(pending)} new inputs", file=sys.stderr, flush=True)
        vectors = np.asarray([cached[key] for key in keys], dtype=np.float32)
        if vectors.ndim != 2 or not np.isfinite(vectors).all() or (np.linalg.norm(vectors, axis=1) == 0).any():
            raise ValueError("Invalid cached vectors; rebuild embedding cache")
        return vectors
