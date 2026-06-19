"""Extractor adapter for docling-serve (REST).

Talks to a running ``docling-serve`` container, requests Markdown output, and
converts it to normalized blocks. docling-serve gives strong PDF layout/table
fidelity.

NOTE: the exact multipart field names for conversion options should be verified
against the running docling-serve version; the response mapping
(``document.md_content``) is what this adapter depends on.
"""

from __future__ import annotations

import json
from typing import Any

import httpx

from fundus.extract.base import ExtractRequest
from fundus.extract.normalize import markdown_to_blocks
from fundus.models import DocMeta, EngineRef, ExtractionResult


class DoclingServeExtractor:
    name = "docling-serve"

    def __init__(
        self,
        url: str,
        version: str = "unknown",
        client: httpx.Client | None = None,
        timeout: float = 600.0,
    ) -> None:
        self._url = url.rstrip("/")
        self.version = version
        self._client = client or httpx.Client(timeout=timeout)

    def extract(self, req: ExtractRequest) -> ExtractionResult:
        do_ocr = req.options.ocr != "off"
        form = {"to_formats": json.dumps(["md"]), "do_ocr": json.dumps(do_ocr)}
        if req.options.ocr_languages:
            form["ocr_lang"] = json.dumps(req.options.ocr_languages)
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
