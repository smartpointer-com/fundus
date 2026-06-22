"""Extractor adapter for docling-serve (REST).

Talks to a running ``docling-serve`` container, requests Markdown output, and
converts it to normalized blocks. docling-serve gives strong PDF layout/table
fidelity.

``/v1/convert/file`` takes multipart form-data: ``files`` plus option fields as
REPEATED form fields (e.g. ``to_formats=md``, not a JSON string). The response is
``{"document": {"md_content": ...}}``.
"""

from __future__ import annotations

import threading
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

import httpx
import structlog

from fundus.extract.base import ExtractRequest
from fundus.extract.normalize import markdown_to_blocks
from fundus.models import DocMeta, EngineRef, ExtractionResult

log = structlog.get_logger("fundus.extract.docling")


def _is_resource_failure(exc: Exception) -> bool:
    """A failure consistent with the engine being out of RAM/time, not a bad document: a dropped
    connection (worker OOM-crashed) or a gateway/timeout status."""
    if isinstance(exc, httpx.TransportError):
        return True
    return isinstance(exc, httpx.HTTPStatusError) and exc.response.status_code in (502, 503, 504)


class DoclingServeExtractor:
    name = "docling-serve"

    def __init__(
        self,
        url: str,
        version: str = "unknown",
        client: httpx.Client | None = None,
        timeout: float = 600.0,
        max_concurrency: int | None = None,
    ) -> None:
        self._url = url.rstrip("/")
        self.version = version
        self._client = client or httpx.Client(timeout=timeout)
        # docling-serve drops queued connections when flooded; bound in-flight requests so the
        # indexing worker pool can be sized for embedding concurrency without overwhelming it.
        self._max_concurrency = max_concurrency
        self._sem = threading.Semaphore(max_concurrency) if max_concurrency else None
        # Serializes exclusive (sequential) retries so two can't deadlock grabbing partial permits.
        self._exclusive_lock = threading.Lock() if max_concurrency else None

    def extract(self, req: ExtractRequest) -> ExtractionResult:
        if self._sem is None:
            return self._extract(req)
        try:
            with self._sem:
                return self._extract(req)
        except Exception as exc:  # noqa: BLE001 - classify, then re-raise or retry exclusively
            if not _is_resource_failure(exc):
                raise
            # Large/slow doc that likely lost a race for RAM/time with other conversions. Retry it
            # with the engine to itself so it gets the full machine — cheaper than sizing the VM
            # for several big scans at once. If it still fails, the error propagates.
            log.info("retrying extraction exclusively", filename=req.filename, error=str(exc))
            with self._exclusive():
                return self._extract(req)

    @contextmanager
    def _exclusive(self) -> Iterator[None]:
        assert self._sem is not None and self._exclusive_lock is not None
        assert self._max_concurrency is not None
        with self._exclusive_lock:  # one exclusive run at a time
            for _ in range(self._max_concurrency):
                self._sem.acquire()
            try:
                yield
            finally:
                for _ in range(self._max_concurrency):
                    self._sem.release()

    def _extract(self, req: ExtractRequest) -> ExtractionResult:
        do_ocr = req.options.ocr != "off"
        # docling-serve wants list options as REPEATED form fields; httpx encodes a dict value
        # that is a list as repeated parts (to_formats -> "md"), not a JSON string.
        form: dict[str, Any] = {"to_formats": ["md"], "do_ocr": str(do_ocr).lower()}
        if req.options.ocr == "force":
            form["force_ocr"] = "true"
        files = {
            "files": (
                req.filename or "document",
                req.data,
                req.mime_type or "application/octet-stream",
            )
        }
        resp = self._client.post(f"{self._url}/v1/convert/file", files=files, data=form)
        resp.raise_for_status()
        return self._to_result(resp.json(), ocr=do_ocr)

    def _to_result(self, payload: dict[str, Any], *, ocr: bool) -> ExtractionResult:
        doc = payload.get("document") or {}
        md = doc.get("md_content") or doc.get("text_content") or ""
        return ExtractionResult(
            engine=EngineRef(name=self.name, version=self.version),
            blocks=markdown_to_blocks(md),
            markdown=md,
            metadata=DocMeta(ocr_used=ocr),
        )
