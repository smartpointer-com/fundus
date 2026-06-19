"""The Extractor interface — one adapter per extraction engine.

Adapters hide the concrete transport (REST/gRPC) and engine-specific quirks
behind a single ``extract`` call that returns a normalized ``ExtractionResult``.
This keeps the engine choice swappable and A/B-testable.
"""

from __future__ import annotations

from typing import Literal, Protocol, runtime_checkable

from pydantic import BaseModel, Field

from fundus.models import ExtractionResult


class ExtractOptions(BaseModel):
    """Normalized superset of options; each adapter maps or ignores per capability."""

    ocr: Literal["auto", "force", "off"] = "auto"
    ocr_languages: list[str] = Field(default_factory=lambda: ["eng"])
    page_range: tuple[int, int] | None = None


class ExtractRequest(BaseModel):
    data: bytes
    mime_type: str
    filename: str | None = None
    options: ExtractOptions = Field(default_factory=ExtractOptions)


@runtime_checkable
class Extractor(Protocol):
    name: str
    version: str

    def extract(self, req: ExtractRequest) -> ExtractionResult: ...
