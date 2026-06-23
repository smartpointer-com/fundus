from types import SimpleNamespace

from fundus.client.cli import _payload, _resolve
from fundus.config import FundusConfig


def _text_block(text):
    return SimpleNamespace(text=text)


def test_payload_unwraps_structured_result_list():
    # FastMCP wraps a list return under {"result": [...]}
    res = SimpleNamespace(structuredContent={"result": [{"parent_id": "A"}]}, content=[])
    assert _payload(res) == [{"parent_id": "A"}]


def test_payload_uses_structured_dict_directly():
    res = SimpleNamespace(structuredContent={"kind": "file", "path": "/x"}, content=[])
    assert _payload(res) == {"kind": "file", "path": "/x"}


def test_payload_falls_back_to_text_json():
    res = SimpleNamespace(structuredContent=None, content=[_text_block('{"a": 1}')])
    assert _payload(res) == {"a": 1}


def test_resolve_derives_endpoint_from_config():
    cfg = FundusConfig.model_validate({"serve": {"host": "127.0.0.1", "port": 9000, "token": "t"}})
    endpoint, token, transport = _resolve(cfg, None, None, None)
    assert endpoint == "http://127.0.0.1:9000/mcp"  # streamable-http default path
    assert token == "t" and transport == "streamable-http"


def test_resolve_rejects_stdio():
    import pytest
    import typer

    cfg = FundusConfig.model_validate({"serve": {"transport": "stdio"}})
    with pytest.raises(typer.BadParameter):
        _resolve(cfg, None, None, None)
