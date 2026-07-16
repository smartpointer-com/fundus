"""Escalating extractor: try a cheap engine first, fall back to a high-quality engine only when
the cheap result looks inadequate.

Most born-digital PDFs (and office documents) extract well and ~10x faster via the cheap engine
(tika). Only the minority that genuinely need OCR or layout analysis — typically scanned PDFs with
no text layer — pay the expensive engine's (docling) cost. For a SEARCH corpus the cheap engine's
flat text is fully searchable even for tables, so we escalate on *emptiness*, not on table
structure (which is hard to detect reliably and rarely matters for retrieval).

When the quality engine ALSO returns ~nothing (some scans rasterize to blank documents in its
pipeline), a final rung re-runs the fast engine with OCR forced — its OCR stack is independent
and often reads pages the quality engine can't. The best of the attempts wins, so a document is
only ever cached empty after all three rungs came up empty.
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
        fast_result: ExtractionResult | None
        try:
            fast_result = self._fast.extract(fast_req)
        except Exception as exc:  # noqa: BLE001 - cheap engine choked; let the quality engine try
            log.info("fast extract failed; escalating", filename=req.filename, error=str(exc))
            fast_result = None
        if fast_result is not None:
            if len(fast_result.markdown.strip()) >= self._min_chars:
                return fast_result
            # A sparse result on a very large file is almost certainly a huge scan (e.g. a whole
            # scanned book). OCRing it can exhaust the quality engine's memory and crash it —
            # taking down every in-flight conversion — and, because a failed extraction is never
            # cached, the same file would re-crash it on every nightly full reconcile. Keep the
            # sparse result.
            if self._max_bytes is not None and len(req.data) > self._max_bytes:
                log.info(
                    "sparse but too large to escalate; keeping fast result",
                    filename=req.filename,
                    bytes=len(req.data),
                )
                return fast_result
            log.info(
                "fast result too sparse; escalating",
                filename=req.filename,
                chars=len(fast_result.markdown.strip()),
            )
        try:
            quality_result = self._quality.extract(req)
        except Exception as exc:  # noqa: BLE001 - classify below; may still be salvageable
            if fast_result is None:
                raise  # both engines down: nothing to salvage from
            log.info(
                "quality extract failed; retrying fast engine with forced OCR",
                filename=req.filename,
                error=str(exc),
            )
            ocr_result = self._fast_ocr(req)
            if ocr_result is None or len(ocr_result.markdown.strip()) < self._min_chars:
                raise  # keep the failure uncached so a transient engine error retries next run
            return ocr_result
        if len(quality_result.markdown.strip()) >= self._min_chars:
            return quality_result
        # The quality engine "succeeded" on ~nothing: typically a scan whose page images its
        # rasterizer can't decode, reported as a blank document rather than an error. The fast
        # engine's OCR is an independent pipeline that often reads such pages — a last, cheap
        # rung before caching an empty extraction forever.
        log.info(
            "quality result also sparse; retrying fast engine with forced OCR",
            filename=req.filename,
            chars=len(quality_result.markdown.strip()),
        )
        results = (self._fast_ocr(req), quality_result, fast_result)
        candidates = [r for r in results if r is not None]
        return max(candidates, key=lambda r: len(r.markdown.strip()))

    def _fast_ocr(self, req: ExtractRequest) -> ExtractionResult | None:
        if req.options.ocr == "off":  # caller explicitly opted out of OCR
            return None
        ocr_req = req.model_copy(
            update={"options": req.options.model_copy(update={"ocr": "force"})}
        )
        try:
            return self._fast.extract(ocr_req)
        except Exception as exc:  # noqa: BLE001 - best-effort rung; fall back to what we have
            log.info("fast OCR retry failed", filename=req.filename, error=str(exc))
            return None
