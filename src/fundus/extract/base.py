"""The Extractor interface — one adapter per extraction engine.

Adapters hide the concrete transport and engine-specific quirks
behind a single ``extract`` call that returns a normalized ``ExtractionResult``.
This keeps the engine choice swappable and A/B-testable.
"""

from __future__ import annotations

from typing import Literal, Protocol

from pydantic import BaseModel, Field

from fundus.models import ExtractionResult


class ExtractOptions(BaseModel):
    """Normalized superset of options; each adapter maps or ignores per capability."""

    ocr: Literal["auto", "force", "off"] = "auto"
    ocr_languages: list[str] = Field(default_factory=lambda: ["eng"])


class ExtractRequest(BaseModel):
    data: bytes
    mime_type: str
    filename: str | None = None
    options: ExtractOptions = Field(default_factory=ExtractOptions)


class Extractor(Protocol):
    name: str
    version: str
    # Output-affecting engine settings, canonicalized (e.g. docling's OCR engine choice).
    # Deliberately EXCLUDES versions, URLs and timeouts: it changes only when configuration
    # that changes the extracted text changes, and is compared (as part of the stored
    # ``extract_sig``) during ``--full`` reconciliation to find stale documents.
    fingerprint: str

    def extract(self, req: ExtractRequest) -> ExtractionResult: ...
