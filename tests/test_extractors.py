import httpx

from fundus.extract.base import ExtractOptions, ExtractRequest
from fundus.extract.docling import DoclingServeExtractor
from fundus.extract.tika import TikaExtractor


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
        return httpx.Response(200, json={"document": {"md_content": "# H\n\nBody."}})

    extractor = DoclingServeExtractor("http://docling:5001", client=_client(handler))
    res = extractor.extract(_req())
    assert res.engine.name == "docling-serve"
    assert any(b.type == "heading" for b in res.blocks)
    assert "Body." in res.markdown
