"""Escalating extractor: try a cheap engine first, fall back to a high-quality engine only when
the cheap result looks inadequate.

Most born-digital PDFs (and office documents) extract well and ~10x faster via the cheap engine
(tika). Only the minority that genuinely need OCR or layout analysis — typically scanned PDFs with
no text layer — pay the expensive engine's (docling) cost. For a SEARCH corpus the cheap engine's
flat text is fully searchable even for tables, so we escalate on *emptiness*, not on table
structure (which is hard to detect reliably and rarely matters for retrieval).
"""

from __future__ import annotations

import structlog

from fundus.extract.base import ExtractRequest, Extractor
from fundus.models import ExtractionResult

log = structlog.get_logger("fundus.extract.router")


class EscalatingExtractor:
    name = "escalate"

    def __init__(
        self,
        fast: Extractor,
        quality: Extractor,
        *,
        min_chars: int = 100,
        max_bytes: int | None = None,
        version: str = "1",
    ) -> None:
        self._fast = fast
        self._quality = quality
        self._min_chars = min_chars
        self._max_bytes = max_bytes
        # Distinct version so escalate results cache separately from a single-engine run.
        self.version = f"{version}+{fast.name}/{quality.name}"

    def extract(self, req: ExtractRequest) -> ExtractionResult:
        # First pass forces OCR off: a scanned PDF then yields ~no text and escalates to the quality
        # engine (which OCRs), instead of the cheap engine doing slow, lower-quality OCR itself.
        fast_req = req.model_copy(
            update={"options": req.options.model_copy(update={"ocr": "off"})}
        )
        try:
            result = self._fast.extract(fast_req)
        except Exception as exc:  # noqa: BLE001 - cheap engine choked; let the quality engine try
            log.info("fast extract failed; escalating", filename=req.filename, error=str(exc))
            return self._quality.extract(req)
        if len(result.markdown.strip()) >= self._min_chars:
            return result
        # A sparse result on a very large file is almost certainly a huge scan (e.g. a whole
        # scanned book). OCRing it can exhaust the quality engine's memory and crash it — taking
        # down every in-flight conversion — and, because a failed extraction is never cached, the
        # same file would re-crash it on every nightly full reconcile. Keep the sparse result.
        if self._max_bytes is not None and len(req.data) > self._max_bytes:
            log.info(
                "sparse but too large to escalate; keeping fast result",
                filename=req.filename,
                bytes=len(req.data),
            )
            return result
        log.info(
            "fast result too sparse; escalating",
            filename=req.filename,
            chars=len(result.markdown.strip()),
        )
        return self._quality.extract(req)
