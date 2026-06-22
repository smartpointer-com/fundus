import httpx

from fundus.extract.base import ExtractOptions, ExtractRequest
from fundus.extract.docling import DoclingServeExtractor
from fundus.extract.router import EscalatingExtractor
from fundus.extract.tika import TikaExtractor
from fundus.models import ExtractionResult


def _client(handler):
    return httpx.Client(transport=httpx.MockTransport(handler))


def _req(**opts):
    return ExtractRequest(
        data=b"x", mime_type="application/pdf", options=ExtractOptions(**opts)
    )


def test_tika_maps_rmeta_to_blocks():
    def handler(request):
        assert request.url.path == "/rmeta/text"
        return httpx.Response(
            200,
            json=[{"X-TIKA:content": "Hello.\n\nWorld.", "dc:title": "Doc", "language": "en"}],
        )

    extractor = TikaExtractor("http://tika:9998", version="3.3.1", client=_client(handler))
    res = extractor.extract(_req())
    assert res.engine.name == "tika"
    assert res.markdown.startswith("Hello.")
    assert [b.type for b in res.blocks] == ["paragraph", "paragraph"]
    assert res.metadata.title == "Doc"
    assert res.metadata.languages == ["en"]


def test_tika_ocr_headers():
    captured: dict[str, str] = {}

    def handler(request):
        captured.update(request.headers)
        return httpx.Response(200, json=[{"X-TIKA:content": "x"}])

    extractor = TikaExtractor("http://tika:9998", client=_client(handler))
    extractor.extract(_req(ocr="force", ocr_languages=["eng", "deu"]))
    assert captured["x-tika-ocrlanguage"] == "eng+deu"
    assert captured["x-tika-pdfocrstrategy"] == "ocr_only"


def test_docling_maps_md_to_blocks():
    def handler(request):
        assert request.url.path == "/v1/convert/file"
        assert b'name="to_formats"' in request.content
        assert b'["md"]' not in request.content  # repeated form field, not a JSON string
        return httpx.Response(200, json={"document": {"md_content": "# H\n\nBody."}})

    extractor = DoclingServeExtractor("http://docling:5001", client=_client(handler))
    res = extractor.extract(_req())
    assert res.engine.name == "docling-serve"
    assert any(b.type == "heading" for b in res.blocks)
    assert "Body." in res.markdown


def test_docling_max_concurrency_caps_in_flight_requests():
    import threading
    import time
    from concurrent.futures import ThreadPoolExecutor

    current = 0
    peak = 0
    lock = threading.Lock()

    def handler(request):
        nonlocal current, peak
        with lock:
            current += 1
            peak = max(peak, current)
        time.sleep(0.02)
        with lock:
            current -= 1
        return httpx.Response(200, json={"document": {"md_content": "x"}})

    extractor = DoclingServeExtractor(
        "http://docling:5001", client=_client(handler), max_concurrency=2
    )
    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(lambda _: extractor.extract(_req()), range(8)))
    assert peak <= 2  # the semaphore bounded concurrent requests despite 8 worker threads


class _FakeEngine:
    def __init__(self, name, text="", fail=False):
        self.name = name
        self.version = "1"
        self._text = text
        self._fail = fail
        self.calls = 0
        self.seen_ocr = []

    def extract(self, req):
        self.calls += 1
        self.seen_ocr.append(req.options.ocr)
        if self._fail:
            raise RuntimeError("engine down")
        return ExtractionResult.from_text(self._text)


def test_escalate_keeps_fast_result_when_sufficient():
    fast, quality = _FakeEngine("tika", "x" * 200), _FakeEngine("docling", "y" * 200)
    res = EscalatingExtractor(fast, quality, min_chars=100).extract(_req())
    assert "x" in res.markdown
    assert fast.calls == 1 and quality.calls == 0


def test_escalate_to_quality_when_fast_sparse():
    fast, quality = _FakeEngine("tika", "tiny"), _FakeEngine("docling", "y" * 200)
    res = EscalatingExtractor(fast, quality, min_chars=100).extract(_req())
    assert "y" in res.markdown and quality.calls == 1


def test_escalate_to_quality_when_fast_errors():
    fast, quality = _FakeEngine("tika", fail=True), _FakeEngine("docling", "y" * 200)
    EscalatingExtractor(fast, quality, min_chars=100).extract(_req())
    assert quality.calls == 1


def test_escalate_forces_ocr_off_on_fast_pass():
    fast, quality = _FakeEngine("tika", "x" * 200), _FakeEngine("docling", "y" * 200)
    EscalatingExtractor(fast, quality, min_chars=100).extract(_req(ocr="force"))
    assert fast.seen_ocr == ["off"]  # cheap pass forced OCR off regardless of caller's request


def test_docling_resource_failure_retries_exclusively():
    calls = {"n": 0}

    def handler(request):
        calls["n"] += 1
        if calls["n"] == 1:
            return httpx.Response(504)  # first attempt times out at the gateway
        return httpx.Response(200, json={"document": {"md_content": "recovered text"}})

    ext = DoclingServeExtractor("http://docling:5001", client=_client(handler), max_concurrency=2)
    res = ext.extract(_req())
    assert "recovered" in res.markdown and calls["n"] == 2  # retried once, exclusively


def test_docling_non_resource_error_not_retried():
    import pytest

    calls = {"n": 0}

    def handler(request):
        calls["n"] += 1
        return httpx.Response(400)  # a bad request is permanent, not a resource limit

    ext = DoclingServeExtractor("http://docling:5001", client=_client(handler), max_concurrency=2)
    with pytest.raises(httpx.HTTPStatusError):
        ext.extract(_req())
    assert calls["n"] == 1  # no exclusive retry


def test_docling_exclusive_holds_all_permits():
    ext = DoclingServeExtractor(
        "http://x",
        client=_client(lambda r: httpx.Response(200, json={"document": {"md_content": "x"}})),
        max_concurrency=3,
    )
    with ext._exclusive():
        assert ext._sem.acquire(blocking=False) is False  # all permits held -> exclusive
    assert ext._sem.acquire(blocking=False) is True  # released afterwards
