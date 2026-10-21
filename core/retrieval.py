"""
core/retrieval.py — FINDS THE KNOWLEDGE MOST SIMILAR TO THE FACTS (cosine similarity).

How it works
  * Every knowledge item and every Manual passage has an "embed_text". Those
    texts were turned into vectors ONCE, during development, by
    scripts/build_index.py (using the developer's own key from .env), and the
    vectors are saved in index/. No API key is stored with them.
  * During a run, only the short search queries (built from the facts) are
    embedded, with the USER's key. Similarity = cosine of the query vector and
    each stored vector (the Day 3 method).
  * Two interchangeable engines rank the vectors:
        ChromaBackend — ChromaDB, the vector database used in Lab 2;
        NumpyBackend  — plain NumPy dot products (as in the Day 3 code).
    ChromaDB's search is approximate (an HNSW index), so its order can differ
    slightly from NumPy's exact order. "auto" uses ChromaDB when it can be imported.
  * If no index has been built yet, KeywordBackend ranks by shared words
    (BM25), so the app still works — just less precisely.

To change the embedding model or vector size: edit EMBEDDING_MODEL /
EMBEDDING_DIMENSIONS in core/config.py, then rebuild the index.
"""

from __future__ import annotations

import json
import math
import re
import sys
import time
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

import numpy as np

from core import config
from core.kb import KnowledgeBase

INDEX_FILE = "vectors.npz"
META_FILE = "meta.json"


# ─────────────────────────────────────────────────────────────────────────────
# 1. The stored index (vectors for items and passages)
# ─────────────────────────────────────────────────────────────────────────────
@dataclass
class VectorIndex:
    item_ids: list[str]
    item_vecs: np.ndarray          # shape (n_items, dims), unit length, float32
    chunk_ids: list[str]
    chunk_vecs: np.ndarray         # shape (n_chunks, dims)
    meta: dict                     # model, dimensions, kb fingerprint, built_at

    @classmethod
    def load(cls, directory: Path = config.INDEX_DIR) -> "VectorIndex | None":
        """Load index/ if present; None if missing or unreadable."""
        npz, meta = Path(directory) / INDEX_FILE, Path(directory) / META_FILE
        if not npz.exists() or not meta.exists():
            return None
        try:
            data = np.load(npz, allow_pickle=False)
            return cls(item_ids=[str(x) for x in data["item_ids"]],
                       item_vecs=data["item_vecs"].astype(np.float32),
                       chunk_ids=[str(x) for x in data["chunk_ids"]],
                       chunk_vecs=data["chunk_vecs"].astype(np.float32),
                       meta=json.loads(meta.read_text(encoding="utf-8")))
        except Exception:
            return None

    def save(self, directory: Path = config.INDEX_DIR) -> None:
        directory = Path(directory)
        directory.mkdir(parents=True, exist_ok=True)
        np.savez_compressed(directory / INDEX_FILE,
                            item_ids=np.array(self.item_ids), item_vecs=self.item_vecs.astype(np.float16),
                            chunk_ids=np.array(self.chunk_ids), chunk_vecs=self.chunk_vecs.astype(np.float16))
        (directory / META_FILE).write_text(json.dumps(self.meta, indent=2), encoding="utf-8")

    def matches(self, kb: KnowledgeBase) -> bool:
        """True if the index was built from this knowledge base with the
        configured embedding model and size."""
        return (self.meta.get("model") == config.EMBEDDING_MODEL
                and self.meta.get("dimensions") == config.EMBEDDING_DIMENSIONS
                and self.meta.get("kb_fingerprint") == kb.fingerprint())


def _unit(rows: np.ndarray) -> np.ndarray:
    norms = np.linalg.norm(rows, axis=1, keepdims=True)
    norms[norms == 0] = 1.0
    return rows / norms


def build_index(kb: KnowledgeBase, embed: Callable[[list[str]], np.ndarray],
                progress: Callable[[float, str], None] | None = None) -> VectorIndex:
    """Embed every item and passage. `embed` takes a list of texts and returns
    an array of vectors (core/llm.py supplies it, with whichever key is in use).
    Used by scripts/build_index.py and by the 'Build index' button."""
    item_ids = sorted(kb.meta)
    chunk_ids = sorted(kb.chunks)
    jobs = [("item", i, kb.embed_text("item", i)) for i in item_ids] + \
           [("chunk", c, kb.embed_text("chunk", c)) for c in chunk_ids]
    vectors: list[np.ndarray] = []
    batch = config.EMBEDDING_BATCH
    for start in range(0, len(jobs), batch):
        texts = [j[2] or " " for j in jobs[start:start + batch]]
        vectors.append(np.asarray(embed(texts), dtype=np.float32))
        if progress:
            progress(min(1.0, (start + batch) / len(jobs)), f"Embedded {min(start + batch, len(jobs))} of {len(jobs)} texts")
    allv = _unit(np.vstack(vectors))
    n = len(item_ids)
    return VectorIndex(item_ids=item_ids, item_vecs=allv[:n], chunk_ids=chunk_ids, chunk_vecs=allv[n:],
                       meta={"model": config.EMBEDDING_MODEL, "dimensions": config.EMBEDDING_DIMENSIONS,
                             "kb_fingerprint": kb.fingerprint(), "built_at": time.strftime("%Y-%m-%d %H:%M"),
                             "items": n, "chunks": len(chunk_ids)})


# ─────────────────────────────────────────────────────────────────────────────
# 2. Ranking engines
#    Each has rank(kind, query, candidate_ids, k) -> [(id, score)], best first.
#    kind is "item" or "chunk"; query is a vector (or text, for keywords).
# ─────────────────────────────────────────────────────────────────────────────
class NumpyBackend:
    name = "NumPy cosine similarity"

    def __init__(self, index: VectorIndex):
        self.index = index
        self.pos = {"item": {i: k for k, i in enumerate(index.item_ids)},
                    "chunk": {c: k for k, c in enumerate(index.chunk_ids)}}

    def rank(self, kind: str, query: np.ndarray, candidate_ids: list[str], k: int):
        matrix = self.index.item_vecs if kind == "item" else self.index.chunk_vecs
        pos = self.pos[kind]
        ids = [c for c in candidate_ids if c in pos]
        if not ids:
            return []
        rows = matrix[[pos[c] for c in ids]]
        q = query / (np.linalg.norm(query) or 1.0)
        scores = rows @ q                                   # cosine (all vectors are unit length)
        order = np.argsort(-scores)[:k]
        return [(ids[j], float(scores[j])) for j in order]


def _import_chromadb():
    """ChromaDB needs SQLite >= 3.35. Streamlit Cloud's system SQLite has been
    older at times; pysqlite3-binary (requirements.txt) supplies a newer one."""
    import sqlite3
    if sqlite3.sqlite_version_info < (3, 35, 0):
        try:
            __import__("pysqlite3")
            sys.modules["sqlite3"] = sys.modules.pop("pysqlite3")
        except ImportError:
            pass
    import chromadb  # noqa: E402
    return chromadb


class ChromaBackend:
    name = "ChromaDB (cosine)"

    def __init__(self, index: VectorIndex):
        chromadb = _import_chromadb()
        from chromadb.config import Settings
        # An in-memory database: rebuilt from the saved vectors when the app
        # starts (a second or two); nothing is written to disk.
        client = chromadb.EphemeralClient(settings=Settings(anonymized_telemetry=False))
        self.collections = {}
        tag = index.meta.get("kb_fingerprint", "kb")
        for kind, ids, vecs in (("item", index.item_ids, index.item_vecs), ("chunk", index.chunk_ids, index.chunk_vecs)):
            col = client.get_or_create_collection(f"vsm_{kind}s_{tag}", metadata={"hnsw:space": "cosine"},
                                                  embedding_function=None)
            if col.count() != len(ids):
                for s in range(0, len(ids), 1000):
                    col.upsert(ids=ids[s:s + 1000], embeddings=vecs[s:s + 1000].tolist())
            self.collections[kind] = (col, set(ids))

    def rank(self, kind: str, query: np.ndarray, candidate_ids: list[str], k: int):
        col, known = self.collections[kind]
        ids = [c for c in candidate_ids if c in known]
        if not ids:
            return []
        res = col.query(query_embeddings=[np.asarray(query, dtype=np.float32).tolist()],
                        n_results=min(k, len(ids)), ids=ids)
        return [(i, 1.0 - float(d)) for i, d in zip(res["ids"][0], res["distances"][0])]


_WORD = re.compile(r"[a-z0-9]+")


class KeywordBackend:
    """BM25 over the embed texts — used only when no vector index exists."""
    name = "keyword ranking (no vector index built)"

    def __init__(self, kb: KnowledgeBase):
        self.kb = kb
        self.docs = {("item", i): self._tokens(kb.embed_text("item", i)) for i in kb.meta}
        self.docs.update({("chunk", c): self._tokens(kb.embed_text("chunk", c)) for c in kb.chunks})
        self.df = Counter()
        for toks in self.docs.values():
            self.df.update(set(toks))
        self.n = len(self.docs)
        self.avg = sum(len(t) for t in self.docs.values()) / max(1, self.n)

    @staticmethod
    def _tokens(text: str) -> list[str]:
        return _WORD.findall((text or "").lower())

    def rank(self, kind: str, query, candidate_ids: list[str], k: int):
        q = set(self._tokens(query if isinstance(query, str) else ""))
        scored = []
        for cid in candidate_ids:
            toks = self.docs.get((kind, cid))
            if toks is None:
                continue
            tf = Counter(toks)
            s = 0.0
            for w in q:
                if w in tf:
                    idf = math.log(1 + (self.n - self.df[w] + 0.5) / (self.df[w] + 0.5))
                    s += idf * tf[w] * 2.2 / (tf[w] + 1.2 * (0.25 + 0.75 * len(toks) / self.avg))
            scored.append((cid, s))
        scored.sort(key=lambda x: -x[1])
        return scored[:k]


# ─────────────────────────────────────────────────────────────────────────────
# 3. The retriever the rest of the app uses
# ─────────────────────────────────────────────────────────────────────────────
class Retriever:
    def __init__(self, kb: KnowledgeBase, index: VectorIndex | None):
        self.kb = kb
        self.index = index if (index is not None and index.matches(kb)) else None
        self.stale_index = index is not None and self.index is None
        self.backend = None
        if self.index is not None:
            choice = config.RETRIEVAL_BACKEND
            if choice in ("auto", "chroma"):
                try:
                    self.backend = ChromaBackend(self.index)
                except Exception:
                    if choice == "chroma":
                        raise
            if self.backend is None:
                self.backend = NumpyBackend(self.index)
        else:
            self.backend = KeywordBackend(kb)

    @property
    def uses_vectors(self) -> bool:
        return self.index is not None

    @property
    def description(self) -> str:
        return self.backend.name

    def rank(self, kind: str, query, candidate_ids: list[str], k: int) -> list[tuple[str, float]]:
        """query: a vector when uses_vectors, else the query text. A text query is
        always ranked by keywords (e.g. when the embeddings request failed)."""
        if isinstance(query, str) and self.uses_vectors:
            if getattr(self, "_keyword", None) is None:     # built once, on first need
                self._keyword = KeywordBackend(self.kb)
            return self._keyword.rank(kind, query, candidate_ids, k)
        return self.backend.rank(kind, query, candidate_ids, k)
