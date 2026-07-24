"""The Sink interface — abstracts the search index (Meilisearch is one impl).

Keeping the sink behind an interface keeps the store swappable and isolates the
index-settings / upsert / delete semantics from the rest of the pipeline.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol

from pydantic import BaseModel, Field

from fundus.models import IndexDocument, ManifestEntry


class IndexSettings(BaseModel):
    searchable: list[str] = Field(default_factory=lambda: ["title", "body"])
    filterable: list[str] = Field(
        default_factory=lambda: [
            "source",
            "item_kind",
            "ts",
            "actors",
            "tags",
            "path",
            "mime",
            "lang",
            "parent_id",
        ]
    )
    sortable: list[str] = Field(default_factory=lambda: ["ts"])
    # Locales declared for localizedAttributes (from the top-level `locales` config).
    locales: list[str] = Field(default_factory=lambda: ["eng"])


class Sink(Protocol):
    def ensure_schema(self, settings: IndexSettings) -> None: ...

    def upsert(self, docs: Sequence[IndexDocument]) -> None: ...

    def delete_missing(self, source: str, live_parent_ids: set[str]) -> int: ...

    def delete_parents(self, parent_ids: set[str]) -> int: ...

    def indexed_manifest(self, source: str) -> dict[str, ManifestEntry]: ...
