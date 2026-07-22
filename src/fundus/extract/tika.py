"""Extractor adapter for Apache Tika Server (REST).

Talks to a running ``apache/tika`` container. Tika returns flat text, so the
result degrades to paragraph blocks. Use the ``-full`` image for OCR.
"""

from __future__ import annotations

from typing import Any

import httpx

from fundus.extract.base import ExtractRequest
from fundus.extract.normalize import text_to_blocks
from fundus.models import DocMeta, EngineRef, ExtractionResult


class TikaExtractor:
    name = "tika"
    fingerprint = ""  # no output-affecting engine settings beyond per-request options

    def __init__(
        self,
        url: str,
        version: str = "unknown",
        client: httpx.Client | None = None,
        timeout: float = 120.0,
    ) -> None:
        self._url = url.rstrip("/")
        self.version = version
        self._client = client or httpx.Client(timeout=timeout)

    def _headers(self, req: ExtractRequest) -> dict[str, str]:
        headers = {"Accept": "application/json"}
        if req.mime_type:
            headers["Content-Type"] = req.mime_type
        if req.options.ocr == "off":
            headers["X-Tika-PDFOcrStrategy"] = "no_ocr"
        elif req.options.ocr == "force":
            headers["X-Tika-PDFOcrStrategy"] = "ocr_only"
        if req.options.ocr_languages:
            headers["X-Tika-OCRLanguage"] = "+".join(req.options.ocr_languages)
        return headers

    def extract(self, req: ExtractRequest) -> ExtractionResult:
        resp = self._client.put(
            f"{self._url}/rmeta/text", content=req.data, headers=self._headers(req)
        )
        resp.raise_for_status()
        return self._to_result(resp.json() or [], ocr=req.options.ocr != "off")

    def _to_result(self, parts: list[dict[str, Any]], *, ocr: bool) -> ExtractionResult:
        meta0 = parts[0] if parts else {}
        texts = [p.get("X-TIKA:content", "").strip() for p in parts]
        text = "\n\n".join(t for t in texts if t)
        lang = meta0.get("language") or meta0.get("dc:language")
        meta = DocMeta(
            title=meta0.get("dc:title") or meta0.get("title"),
            languages=[lang] if lang else [],
            ocr_used=ocr,
        )
        return ExtractionResult(
            engine=EngineRef(name=self.name, version=self.version),
            engine_fingerprint=self.fingerprint,
            blocks=text_to_blocks(text),
            markdown=text,
            metadata=meta,
        )
