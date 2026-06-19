from fundus.serve.mcp import build_mcp, search_tool


class FakeSink:
    def __init__(self):
        self.called_with = None

    def search(self, query, *, semantic_ratio, filters, limit):
        self.called_with = {
            "query": query,
            "semantic_ratio": semantic_ratio,
            "filters": filters,
            "limit": limit,
        }
        return [{"parent_id": "A", "chunks": [{"id": "1"}]}]


def test_search_tool_delegates_to_sink():
    sink = FakeSink()
    out = search_tool(sink, "hello", limit=5, semantic_ratio=0.7, filters="source = mail")
    assert out[0]["parent_id"] == "A"
    assert sink.called_with == {
        "query": "hello",
        "semantic_ratio": 0.7,
        "filters": "source = mail",
        "limit": 5,
    }


def test_build_mcp_registers_tool_without_error():
    server = build_mcp(FakeSink())
    assert server is not None
