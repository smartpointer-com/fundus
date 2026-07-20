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


def test_docling_sends_ocr_engine_when_configured():
    def handler(request):
        assert b'name="ocr_engine"' in request.content
        assert b"ocrmac" in request.content
        return httpx.Response(200, json={"document": {"md_content": "x"}})

    ext = DoclingServeExtractor("http://docling:5001", client=_client(handler), ocr_engine="ocrmac")
    ext.extract(_req())


def test_docling_omits_ocr_engine_by_default():
    def handler(request):
        assert b'name="ocr_engine"' not in request.content  # let docling-serve use its own default
        return httpx.Response(200, json={"document": {"md_content": "x"}})

    ext = DoclingServeExtractor("http://docling:5001", client=_client(handler))
    ext.extract(_req())


def test_docling_omits_ocr_engine_when_ocr_off():
    # Engine selection is meaningless with OCR disabled — the request stays self-consistent.
    def handler(request):
        assert b'name="ocr_engine"' not in request.content
        return httpx.Response(200, json={"document": {"md_content": "x"}})

    ext = DoclingServeExtractor("http://docling:5001", client=_client(handler), ocr_engine="ocrmac")
    ext.extract(_req(ocr="off"))


def test_docling_salvages_ocr_text_from_picture_pages():
    # A scanned page classified as one full-page picture: md_content collapses to an image
    # placeholder while the OCR text items sit inside the picture in json_content.
    document = {
        "md_content": "<!-- image -->",
        "json_content": {
            "texts": [
                {"text": "TAXICAB FARE", "parent": {"$ref": "#/pictures/0"}},
                {"text": "  ", "parent": {"$ref": "#/pictures/0"}},  # blank: dropped
                {"text": "Body prose.", "parent": {"$ref": "#/body"}},  # in md already
            ],
        },
    }

    def handler(request):
        assert b'name="to_formats"' in request.content
        return httpx.Response(200, json={"document": document})

    extractor = DoclingServeExtractor("http://docling:5001", client=_client(handler))
    res = extractor.extract(_req())
    assert [b.text for b in res.blocks if b.type == "paragraph"] == ["TAXICAB FARE"]
    assert "TAXICAB FARE" in res.markdown  # visible to the escalation router's sparseness check
    assert "Body prose." not in res.markdown  # body-parented text is not double-added


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
    def __init__(self, name, text="", fail=False, ocr_text=None):
        self.name = name
        self.version = "1"
        self._text = text
        self._fail = fail
        self._ocr_text = ocr_text  # returned instead of text when OCR is forced
        self.calls = 0
        self.seen_ocr = []

    def extract(self, req):
        self.calls += 1
        self.seen_ocr.append(req.options.ocr)
        if self._fail:
            raise RuntimeError("engine down")
        if req.options.ocr == "force" and self._ocr_text is not None:
            return ExtractionResult.from_text(self._ocr_text)
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


def test_escalate_skips_oversized_files():
    # A sparse fast result on a huge file (a scanned book) must NOT escalate: OCRing it can
    # crash the quality engine. The sparse fast result is kept instead.
    fast, quality = _FakeEngine("tika", "tiny"), _FakeEngine("docling", "y" * 200)
    big = ExtractRequest(data=b"x" * 1000, mime_type="application/pdf", options=ExtractOptions())
    res = EscalatingExtractor(fast, quality, min_chars=100, max_bytes=999).extract(big)
    assert "tiny" in res.markdown and quality.calls == 0
    # ...but a file at/below the cap still escalates.
    small = ExtractRequest(data=b"x" * 999, mime_type="application/pdf", options=ExtractOptions())
    res = EscalatingExtractor(fast, quality, min_chars=100, max_bytes=999).extract(small)
    assert "y" in res.markdown and quality.calls == 1


def test_escalate_forces_ocr_off_on_fast_pass():
    fast, quality = _FakeEngine("tika", "x" * 200), _FakeEngine("docling", "y" * 200)
    EscalatingExtractor(fast, quality, min_chars=100).extract(_req(ocr="force"))
    assert fast.seen_ocr == ["off"]  # cheap pass forced OCR off regardless of caller's request


def test_escalate_retries_fast_with_ocr_when_quality_also_sparse():
    # A scan the quality engine rasterizes to a blank document: both rungs sparse, but the
    # fast engine's own OCR reads it. Its result must win instead of caching empty forever.
    fast = _FakeEngine("tika", text="", ocr_text="z" * 200)
    quality = _FakeEngine("docling", "")
    res = EscalatingExtractor(fast, quality, min_chars=100).extract(_req())
    assert "z" in res.markdown
    assert fast.seen_ocr == ["off", "force"] and quality.calls == 1


def test_escalate_keeps_best_result_when_all_rungs_sparse():
    # All three attempts sparse: the longest one wins (here the quality engine's).
    fast = _FakeEngine("tika", text="a", ocr_text="bb")
    quality = _FakeEngine("docling", "ccc")
    res = EscalatingExtractor(fast, quality, min_chars=100).extract(_req())
    assert res.markdown == "ccc"


def test_escalate_no_ocr_retry_when_caller_disabled_ocr():
    fast = _FakeEngine("tika", text="", ocr_text="z" * 200)
    quality = _FakeEngine("docling", "")
    res = EscalatingExtractor(fast, quality, min_chars=100).extract(_req(ocr="off"))
    assert fast.seen_ocr == ["off"]  # no forced-OCR pass against the caller's wishes
    assert res.markdown == ""


def test_escalate_quality_failure_salvaged_by_fast_ocr():
    fast = _FakeEngine("tika", text="", ocr_text="z" * 200)
    quality = _FakeEngine("docling", fail=True)
    res = EscalatingExtractor(fast, quality, min_chars=100).extract(_req())
    assert "z" in res.markdown  # engine failure rescued by the OCR rung


def test_escalate_quality_failure_reraised_when_ocr_sparse():
    import pytest

    # If the OCR rung can't produce a usable result either, the quality failure must
    # propagate (uncached) so a transient engine error is retried on a later run.
    fast = _FakeEngine("tika", text="", ocr_text="")
    quality = _FakeEngine("docling", fail=True)
    with pytest.raises(RuntimeError):
        EscalatingExtractor(fast, quality, min_chars=100).extract(_req())


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
