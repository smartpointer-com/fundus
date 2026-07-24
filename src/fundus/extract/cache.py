"""Content-addressed extraction cache + the cache-aware extract helper.

Keyed by (content hash, engine, engine version, options) so multiple engines'
extractions of the same document coexist, and re-chunking / re-embedding never
re-extracts (never re-OCRs).
"""

from __future__ import annotations

import sqlite3
import time
from contextlib import closing
from pathlib import Path
from typing import Protocol

from pydantic import BaseModel, ConfigDict

from fundus.core.ids import content_sha256, options_hash
from fundus.core.sqlite import connect as sqlite_connect
from fundus.extract.base import Extractor, ExtractRequest
from fundus.models import ExtractionResult


class CacheKey(BaseModel):
    model_config = ConfigDict(frozen=True)

    content_sha256: str
    engine: str
    engine_version: str
    options_hash: str

    def as_str(self) -> str:
        return f"{self.content_sha256}:{self.engine}:{self.engine_version}:{self.options_hash}"


class ExtractionCache(Protocol):
    def get(self, key: CacheKey) -> ExtractionResult | None: ...

    def put(self, key: CacheKey, result: ExtractionResult) -> None: ...


class SqliteExtractionCache:
    """SQLite-backed cache. One row per (content, engine, version, options)."""

    def __init__(self, path: str | Path) -> None:
        self._path = str(path)
        Path(self._path).parent.mkdir(parents=True, exist_ok=True)
        with closing(self._connect()) as conn, conn:
            conn.execute(
                "CREATE TABLE IF NOT EXISTS extractions ("
                "key TEXT PRIMARY KEY, result TEXT NOT NULL, created REAL NOT NULL)"
            )

    def _connect(self) -> sqlite3.Connection:
        return sqlite_connect(self._path)  # per-call connection; see core.sqlite for the why

    def get(self, key: CacheKey) -> ExtractionResult | None:
        with closing(self._connect()) as conn:
            row = conn.execute(
                "SELECT result FROM extractions WHERE key = ?", (key.as_str(),)
            ).fetchone()
        return ExtractionResult.model_validate_json(row[0]) if row else None

    def put(self, key: CacheKey, result: ExtractionResult) -> None:
        with closing(self._connect()) as conn, conn:
            conn.execute(
                "INSERT OR REPLACE INTO extractions (key, result, created) VALUES (?, ?, ?)",
                (key.as_str(), result.model_dump_json(), time.time()),
            )


def cache_key(extractor: Extractor, req: ExtractRequest) -> CacheKey:
    return CacheKey(
        content_sha256=content_sha256(req.data),
        engine=extractor.name,
        engine_version=extractor.version,
        options_hash=options_hash(req.options),
    )


def extract_with_cache(
    extractor: Extractor, cache: ExtractionCache, req: ExtractRequest, *, refresh: bool = False
) -> ExtractionResult:
    """Return a cached extraction if present, else extract once and store it.

    ``refresh`` skips the cache read (but still writes): the deliberate-staleness path.
    The cache key ignores engine settings and live engine versions, so re-extracting
    after a config change or engine upgrade requires bypassing the read — the fresh
    result then overwrites the stale row under the same key.
    """
    key = cache_key(extractor, req)
    if not refresh:
        hit = cache.get(key)
        if hit is not None:
            return hit
    result = extractor.extract(req)
    cache.put(key, result)
    return result
