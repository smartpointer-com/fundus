import asyncio

import pytest

from fundus.config import FundusConfig
from fundus.serve.auth import bearer_guard
from fundus.serve.mcp import build_mcp, locate_tool, search_tool, sources_tool


class FakeSink:
    def __init__(self, counts=None):
        self.called_with = None
        self._counts = counts or {}

    def search(self, query, *, semantic_ratio, filters, limit):
        self.called_with = {
            "query": query, "semantic_ratio": semantic_ratio, "filters": filters, "limit": limit,
        }
        return [{"parent_id": "A", "chunks": [{"id": "1"}]}]

    def source_counts(self):
        return dict(self._counts)


def _config(**sources):
    data = {"sources": [{"name": n, "type": t, **kw} for n, (t, kw) in sources.items()]}
    return FundusConfig.model_validate(data)


def test_search_tool_delegates_to_sink():
    sink = FakeSink()
    out = search_tool(sink, "hello", limit=5, semantic_ratio=0.7)
    assert out[0]["parent_id"] == "A"
    assert sink.called_with == {
        "query": "hello", "semantic_ratio": 0.7, "filters": None, "limit": 5,
    }


def test_search_tool_compiles_typed_params_into_meili_filter():
    sink = FakeSink()
    search_tool(sink, "q", source="mail", item_kind="file", since=1000, until=2000)
    assert sink.called_with["filters"] == (
        'source = "mail" AND item_kind = "file" AND ts >= 1000 AND ts <= 2000'
    )


def test_search_tool_ands_raw_filter_with_typed_params():
    sink = FakeSink()
    search_tool(sink, "q", source="mail", filters='tags = "attachment"')
    assert sink.called_with["filters"] == 'source = "mail" AND (tags = "attachment")'


def test_search_tool_rewrites_bad_filter_into_guidance():
    class BadSink:
        def search(self, *a, **k):
            raise RuntimeError("invalid_search_filter: expected operator = or IN")

    # a Lucene-style `source:mail` must come back as actionable guidance, not an opaque API error
    with pytest.raises(ValueError, match="field:value"):
        search_tool(BadSink(), "q", filters="source:mail")


def test_sources_tool_merges_config_and_counts():
    sink = FakeSink(counts={"docs": 42})
    cfg = _config(docs=("files", {"roots": ["/corpus"]}), mail=("notmuch", {"db": "/maildir"}))
    rows = {r["name"]: r for r in sources_tool(sink, cfg)}
    assert rows["docs"]["roots"] == ["/corpus"] and rows["docs"]["indexed_docs"] == 42
    assert rows["mail"]["db"] == "/maildir" and rows["mail"]["indexed_docs"] == 0  # uncounted -> 0


def test_locate_tool_passes_through_file_paths(tmp_path):
    f = tmp_path / "doc.pdf"
    f.write_text("x")
    out = locate_tool(_config(), str(f))
    assert out == {"kind": "file", "path": str(f), "exists": True}
    missing = locate_tool(_config(), "/no/such/file")
    assert missing["kind"] == "file" and missing["exists"] is False


def test_locate_tool_unknown_ref_without_notmuch():
    assert locate_tool(_config(), "abc@example")["kind"] == "unknown"


def test_build_mcp_registers_tools_without_error():
    server = build_mcp(FakeSink(), _config())
    assert server is not None


# --- bearer-token gate ----------------------------------------------------------------------


def _drive(headers):
    """Run the guarded ASGI app for one HTTP request; return (status, body)."""
    sent = []

    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(msg):
        sent.append(msg)

    async def inner(scope, receive, send):  # the wrapped app: always 200 OK
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"ok"})

    guarded = bearer_guard(inner, "s3cret")
    asyncio.run(guarded({"type": "http", "headers": headers}, receive, send))
    status = next(m["status"] for m in sent if m["type"] == "http.response.start")
    body = b"".join(m.get("body", b"") for m in sent if m["type"] == "http.response.body")
    return status, body


def test_bearer_guard_rejects_missing_or_wrong_token():
    assert _drive([])[0] == 401
    assert _drive([(b"authorization", b"Bearer nope")])[0] == 401


def test_bearer_guard_allows_correct_token():
    status, body = _drive([(b"authorization", b"Bearer s3cret")])
    assert status == 200 and body == b"ok"


def test_resolve_search_key_prefers_search_then_admin():
    from fundus.core.pipeline import resolve_search_key

    with pytest.raises(RuntimeError, match="Meili key"):
        resolve_search_key(FundusConfig())  # neither key set
    admin_only = FundusConfig.model_validate({"meilisearch": {"api_key": "ADMIN"}})
    assert resolve_search_key(admin_only) == "ADMIN"  # falls back to the admin key
    both = FundusConfig.model_validate({"meilisearch": {"api_key": "ADMIN", "search_key": "SEARCH"}})
    assert resolve_search_key(both) == "SEARCH"  # scoped key wins when present
