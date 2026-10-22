"""
core/case_store.py — WHERE THE CASE DATABASE IS KEPT ON DISK.

The cases the user chunks on the Case database page are saved in the app's own files, under cases/
(config.CASES_DIR), and loaded again when a session starts:

  cases/case_database.sqlite3   SQLite — every case (its fields as JSON) and its embedding, plus the database's
                                details (format, embedding model, when it was saved). This file is the record of
                                the database: commit it to give every user of the deployed app the same cases.
  cases/chroma/                 ChromaDB (a persistent client) — the same embeddings in a collection searched by
                                cosine similarity when the estimate picks the most similar cases. It is rebuilt
                                from the SQLite file whenever it is missing or out of step, so it need not be
                                committed (.gitignore leaves it out). If ChromaDB cannot be imported, the cases
                                are ranked with NumPy instead (the same cosine similarity, computed exactly).

  CaseStore.load()    the saved database (None if nothing has been saved yet); raises StoreError if damaged
  CaseStore.save(db)  write the whole database (one transaction), then bring the ChromaDB collection into step
  CaseStore.info()    what is on disk, for the page's status line

Nothing here holds an API key: the embeddings were made with the user's key at chunking time and are plain
numbers. The JSON download/upload on the page (CaseDB.to_json / from_json) stays as the portable copy.
"""

from __future__ import annotations

import functools
import json
import re
import sqlite3
import time
from pathlib import Path

import numpy as np

from core import config
from core.cases import DB_FORMAT, CaseDB, _vec_to_b64

SQLITE_FILE = "case_database.sqlite3"
CHROMA_DIR = "chroma"
CHROMA_BATCH = 500


class StoreError(Exception):
    """The saved database could not be read or written (the message says why)."""


def store_dir() -> Path:
    return Path(config.CASES_DIR)


# ─────────────────────────────────────────────────────────────────────────────
# ChromaDB: the embeddings, searched by cosine similarity
# ─────────────────────────────────────────────────────────────────────────────
def collection_name(model: str = None, dims: int = None) -> str:
    """One collection per embedding model and size: vectors from another model cannot be compared."""
    raw = f"cases_{model or config.EMBEDDING_MODEL}_{dims or config.EMBEDDING_DIMENSIONS}"
    name = re.sub(r"[^A-Za-z0-9_.-]", "-", raw)[:63]
    return name if name[-1].isalnum() else name + "0"


@functools.lru_cache(maxsize=None)
def _chroma_client(path: str):
    """One persistent client per folder (ChromaDB keeps one system per path within a process)."""
    from core.retrieval import _import_chromadb          # the SQLite >= 3.35 shim for Streamlit Cloud
    chromadb = _import_chromadb()
    from chromadb.config import Settings
    Path(path).mkdir(parents=True, exist_ok=True)
    return chromadb.PersistentClient(path=path, settings=Settings(anonymized_telemetry=False))


class CaseVectors:
    """The persistent ChromaDB collection of case embeddings under `folder`. Every method is best-effort: a
    failure sets `error` and the caller falls back to NumPy, so the cases themselves are never at risk."""

    def __init__(self, folder: Path):
        self.folder = Path(folder)
        self.error = ""

    @property
    def available(self) -> bool:
        return not self.error

    def _collection(self):
        client = _chroma_client(str(self.folder.resolve()))
        name = collection_name()
        for other in client.list_collections():            # an older model's collection: stale, delete it
            other_name = other if isinstance(other, str) else other.name
            if other_name.startswith("cases_") and other_name != name:
                client.delete_collection(other_name)
        return client.get_or_create_collection(name, metadata={"hnsw:space": "cosine"}, embedding_function=None)

    def sync(self, db: CaseDB) -> int:
        """Bring the collection into step with the database's vectors: delete the ids no longer there, add the ones
        missing (a case's id is never reused, and its vector never changes once made). Returns the count held."""
        try:
            col = self._collection()
            have = set(col.get(include=[])["ids"])
            want = {k for k in db.vectors if k in {c.id for c in db.cases}}
            gone = sorted(have - want)
            for s in range(0, len(gone), CHROMA_BATCH):
                col.delete(ids=gone[s:s + CHROMA_BATCH])
            by_id = {c.id: c for c in db.cases}
            new = sorted(want - have, key=lambda k: int(k[1:]) if k[1:].isdigit() else 0)
            for s in range(0, len(new), CHROMA_BATCH):
                ids = new[s:s + CHROMA_BATCH]
                col.upsert(ids=ids, embeddings=[db.vectors[k].astype(np.float32).tolist() for k in ids],
                           documents=[(by_id[k].citation + " — " + by_id[k].summary)[:2000] for k in ids],
                           metadatas=[{"citation": by_id[k].citation, "court": by_id[k].court,
                                       "principal_offence_id": by_id[k].principal_offence_id or "",
                                       "year": by_id[k].year or 0} for k in ids])
            self.error = ""
            return col.count()
        except Exception as exc:  # noqa: BLE001 — recorded and shown; NumPy ranks the cases meanwhile
            self.error = f"{type(exc).__name__}: {exc}"
            return 0

    def count(self) -> int:
        try:
            return self._collection().count()
        except Exception as exc:  # noqa: BLE001
            self.error = f"{type(exc).__name__}: {exc}"
            return 0

    def similarities(self, query: np.ndarray, ids: list[str]) -> dict[str, float] | None:
        """Cosine similarity of `query` with each of `ids`, from the collection; None if it cannot answer for
        every id (the caller then uses NumPy)."""
        if not ids:
            return {}
        try:
            col = self._collection()
            n = min(len(ids), col.count())
            if n < len(ids):
                return None
            q = np.asarray(query, dtype=np.float32)
            q = q / (float(np.linalg.norm(q)) or 1.0)
            res = col.query(query_embeddings=[q.tolist()], n_results=n, ids=list(ids), include=["distances"])
            out = {i: 1.0 - float(d) for i, d in zip(res["ids"][0], res["distances"][0])}
            self.error = ""
            return out if len(out) == len(ids) else None
        except Exception as exc:  # noqa: BLE001
            self.error = f"{type(exc).__name__}: {exc}"
            return None


# ─────────────────────────────────────────────────────────────────────────────
# SQLite: the cases
# ─────────────────────────────────────────────────────────────────────────────
SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS cases (
    id        TEXT PRIMARY KEY,         -- C1, C2 … (never reused)
    position  INTEGER NOT NULL,         -- the order on the page
    data      TEXT NOT NULL,            -- the case's fields as JSON (CaseRecord.to_dict)
    vector    BLOB                      -- its embedding, float32 unit vector (NULL if not embedded yet)
);
"""


class CaseStore:
    def __init__(self, folder: Path | None = None):
        self.folder = Path(folder) if folder else store_dir()
        self.path = self.folder / SQLITE_FILE
        self.vectors = CaseVectors(self.folder / CHROMA_DIR)

    def exists(self) -> bool:
        return self.path.exists()

    def _connect(self) -> sqlite3.Connection:
        self.folder.mkdir(parents=True, exist_ok=True)
        con = sqlite3.connect(self.path, timeout=30)
        con.executescript(SCHEMA)
        return con

    def save(self, db: CaseDB) -> None:
        """Write the whole database in one transaction (the file always holds a complete database), then
        bring the ChromaDB collection into step. Raises StoreError if the file cannot be written."""
        ids = {c.id for c in db.cases}
        rows = [(c.id, n, json.dumps(c.to_dict(), ensure_ascii=False),
                 db.vectors[c.id].astype(np.float32).tobytes() if c.id in db.vectors else None)
                for n, c in enumerate(db.cases)]
        meta = {"format": DB_FORMAT, "version": "1", "saved": time.strftime("%Y-%m-%d %H:%M"),
                "embedding_model": config.EMBEDDING_MODEL, "dimensions": str(config.EMBEDDING_DIMENSIONS),
                "last_number": str(db.last_number), "origin": db.origin or ""}
        try:
            con = self._connect()
            try:
                with con:
                    con.execute("DELETE FROM cases")
                    con.executemany("INSERT INTO cases (id, position, data, vector) VALUES (?, ?, ?, ?)", rows)
                    con.executemany("INSERT OR REPLACE INTO meta (key, value) VALUES (?, ?)", list(meta.items()))
            finally:
                con.close()
        except sqlite3.Error as exc:
            raise StoreError(f"The case database could not be saved to {self.path.name}: {exc}") from None
        db.vectors = {k: v for k, v in db.vectors.items() if k in ids}
        self.vectors.sync(db)
        db.search = self.vectors.similarities

    def load(self) -> CaseDB | None:
        """The saved database, checked case by case like a JSON file (CaseDB.from_json); None if nothing has been
        saved. Raises StoreError if the file is damaged."""
        if not self.exists():
            return None
        try:
            con = sqlite3.connect(self.path, timeout=30)
            try:
                con.executescript(SCHEMA)
                meta = dict(con.execute("SELECT key, value FROM meta").fetchall())
                rows = con.execute("SELECT id, data, vector FROM cases ORDER BY position").fetchall()
            finally:
                con.close()
        except sqlite3.Error as exc:
            raise StoreError(f"{self.path.name} could not be read: {exc}") from None
        cases, vectors = [], {}
        for cid, data, blob in rows:
            try:
                cases.append(json.loads(data))
            except json.JSONDecodeError:
                raise StoreError(f"{self.path.name}: case {cid} is not valid JSON.") from None
            if blob:
                vec = np.frombuffer(blob, dtype=np.float32)
                vectors[cid] = _vec_to_b64(vec)
        payload = {"format": meta.get("format", DB_FORMAT), "version": 1, "saved": meta.get("saved", ""),
                   "embedding_model": meta.get("embedding_model"), "last_number": int(meta.get("last_number") or 0),
                   "cases": cases, "vectors": vectors}
        try:
            payload["dimensions"] = int(meta.get("dimensions") or 0)
        except ValueError:
            payload["dimensions"] = 0
        try:
            db = CaseDB.from_json(json.dumps(payload), origin=f"cases/{SQLITE_FILE}")
        except ValueError as exc:
            raise StoreError(f"{self.path.name}: {exc}") from None
        db.origin = meta.get("origin") or db.origin
        self.vectors.sync(db)                                 # the collection follows the file
        db.search = self.vectors.similarities
        return db

    def clear(self) -> None:
        """Remove the saved database (the files are deleted)."""
        if self.path.exists():
            self.path.unlink()
        self.vectors.sync(CaseDB())

    def info(self) -> dict:
        """What is on disk: {'saved', 'cases', 'vectors', 'chroma', 'chroma_error'}."""
        out = {"saved": "", "cases": 0, "vectors": 0, "chroma": 0, "chroma_error": ""}
        if self.exists():
            try:
                con = sqlite3.connect(self.path, timeout=30)
                try:
                    con.executescript(SCHEMA)
                    out["saved"] = dict(con.execute("SELECT key, value FROM meta").fetchall()).get("saved", "")
                    out["cases"], out["vectors"] = con.execute(
                        "SELECT COUNT(*), COUNT(vector) FROM cases").fetchone()
                finally:
                    con.close()
            except sqlite3.Error as exc:
                out["chroma_error"] = f"{type(exc).__name__}: {exc}"
        if (self.folder / CHROMA_DIR).exists() or out["cases"]:
            out["chroma"] = self.vectors.count()
        out["chroma_error"] = out["chroma_error"] or self.vectors.error
        return out
