"""Content-addressed extraction cache.

Keyed by (content hash, engine, engine version, options) so multiple engines'
extractions of the same document coexist — enabling A/B comparison and making
re-chunking / re-embedding free of re-extraction (in particular, never re-OCRing).
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict

from fundus.models import ExtractionResult


class CacheKey(BaseModel):
    model_config = ConfigDict(frozen=True)

    content_sha256: str
    engine: str
    engine_version: str
    options_hash: str


@runtime_checkable
class ExtractionCache(Protocol):
    def get(self, key: CacheKey) -> ExtractionResult | None: ...

    def put(self, key: CacheKey, result: ExtractionResult) -> None: ...
