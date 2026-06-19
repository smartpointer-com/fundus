"""Deterministic id and content hashing helpers."""

from __future__ import annotations

import hashlib
import json
from typing import Any

from pydantic import BaseModel


def content_sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _blake(*parts: str) -> str:
    h = hashlib.blake2b(digest_size=16)
    for part in parts:
        h.update(part.encode("utf-8"))
        h.update(b"\x00")  # domain separator so ("a","b") != ("ab",)
    return h.hexdigest()


def doc_id(source: str, native_id: str, seq: int) -> str:
    """Stable id for an index document (one chunk)."""
    return _blake(source, native_id, str(seq))


def parent_id(source: str, native_id: str) -> str:
    """Stable id for the parent artifact a chunk belongs to."""
    return _blake(source, native_id)


def options_hash(options: BaseModel | dict[str, Any]) -> str:
    """Stable hash of extraction options, for cache keying."""
    data = options.model_dump() if isinstance(options, BaseModel) else options
    canonical = json.dumps(data, sort_keys=True, separators=(",", ":"), default=str)
    return _blake(canonical)
