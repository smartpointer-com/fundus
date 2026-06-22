"""SQLite cache of document embedding vectors, keyed by (model, embed-input text).

Lets a re-index reuse vectors for unchanged documents instead of recomputing them, so a re-run
that only adds or fixes a few items stays cheap. Vectors don't depend on metadata like native_id,
so adding such fields never invalidates the cache. Can be backfilled from vectors already stored
in the index (see ``fundus.embed.backfill``).
"""

from __future__ import annotations

import array
import hashlib
import sqlite3
from collections.abc import Sequence
from pathlib import Path


def embed_input_text(title: str | None, body: str) -> str:
    """The exact text embedded for a document.

    The single source of truth shared by the indexing path and the cache key, so the two can
    never drift out of sync.
    """
    return f"{title or ''} {body}".strip()


def _encode(vector: Sequence[float]) -> bytes:
    return array.array("f", vector).tobytes()


def _decode(blob: bytes) -> list[float]:
    arr = array.array("f")
    arr.frombytes(blob)
    return arr.tolist()


class SqliteEmbeddingCache:
    """Maps (model, text) -> vector. Each call opens its own connection (worker-pool safe)."""

    def __init__(self, path: str, model: str) -> None:
        self._path = path
        self._model = model
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as conn:
            conn.execute("CREATE TABLE IF NOT EXISTS embeddings (key TEXT PRIMARY KEY, vec BLOB)")

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self._path, timeout=30.0)
        conn.execute("PRAGMA journal_mode=WAL")
        return conn

    def _key(self, text: str) -> str:
        return hashlib.sha256(f"{self._model}\x00{text}".encode()).hexdigest()

    def get_many(self, texts: list[str]) -> list[list[float] | None]:
        keys = [self._key(t) for t in texts]
        found: dict[str, list[float]] = {}
        with self._connect() as conn:
            for i in range(0, len(keys), 400):
                batch = keys[i : i + 400]
                placeholders = ",".join("?" * len(batch))
                for key, blob in conn.execute(
                    f"SELECT key, vec FROM embeddings WHERE key IN ({placeholders})", batch
                ):
                    found[key] = _decode(blob)
        return [found.get(k) for k in keys]

    def put_many(self, texts: list[str], vectors: list[list[float]]) -> None:
        rows = [(self._key(t), _encode(v)) for t, v in zip(texts, vectors, strict=True)]
        with self._connect() as conn:
            conn.executemany("INSERT OR REPLACE INTO embeddings (key, vec) VALUES (?, ?)", rows)
