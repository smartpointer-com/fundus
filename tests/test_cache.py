from fundus.extract.base import ExtractOptions, ExtractRequest
from fundus.extract.cache import (
    CacheKey,
    SqliteExtractionCache,
    cache_key,
    extract_with_cache,
)
from fundus.models import Block, DocMeta, EngineRef, ExtractionResult


class CountingExtractor:
    name = "fake"
    version = "1"

    def __init__(self):
        self.calls = 0

    def extract(self, req):
        self.calls += 1
        text = req.data.decode()
        return ExtractionResult(
            engine=EngineRef(name=self.name, version=self.version),
            blocks=[Block(type="paragraph", text=text)],
            markdown=text,
            metadata=DocMeta(),
        )


def _req(data=b"hello"):
    return ExtractRequest(data=data, mime_type="text/plain", options=ExtractOptions())


def test_sqlite_cache_roundtrip(tmp_path):
    cache = SqliteExtractionCache(tmp_path / "c.db")
    key = CacheKey(content_sha256="x", engine="e", engine_version="1", options_hash="o")
    assert cache.get(key) is None
    cache.put(key, ExtractionResult(engine=EngineRef(name="e", version="1"), markdown="hi"))
    got = cache.get(key)
    assert got is not None and got.markdown == "hi"


def test_extract_with_cache_calls_once(tmp_path):
    cache = SqliteExtractionCache(tmp_path / "c.db")
    extractor = CountingExtractor()
    r1 = extract_with_cache(extractor, cache, _req())
    r2 = extract_with_cache(extractor, cache, _req())
    assert extractor.calls == 1
    assert r1.markdown == r2.markdown == "hello"


def test_cache_key_varies_by_content():
    extractor = CountingExtractor()
    assert cache_key(extractor, _req(b"a")) != cache_key(extractor, _req(b"b"))


def test_extract_with_cache_refresh_bypasses_read_and_overwrites(tmp_path):
    class Versioned(CountingExtractor):
        def extract(self, req):
            res = super().extract(req)
            res.markdown = f"v{self.calls}"
            return res

    cache = SqliteExtractionCache(tmp_path / "c.db")
    extractor = Versioned()
    assert extract_with_cache(extractor, cache, _req()).markdown == "v1"
    # refresh skips the cache read: the engine runs again despite the existing row
    assert extract_with_cache(extractor, cache, _req(), refresh=True).markdown == "v2"
    assert extractor.calls == 2
    # ...and the fresh result overwrote the row, so normal reads now serve it
    assert extract_with_cache(extractor, cache, _req()).markdown == "v2"
    assert extractor.calls == 2
