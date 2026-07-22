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


def test_failure_message_unauthorized_distinguishes_no_token_from_wrong_token():
    from fundus.client.cli import _failure_message

    no_token = _failure_message("http://h/mcp", False, Exception("unauthorized"))
    assert "no bearer token" in no_token and "FUNDUS_SERVE_TOKEN" in no_token
    wrong = _failure_message("http://h/mcp", True, Exception("Client error '401 Unauthorized'"))
    assert "rejected the token" in wrong


def test_failure_message_other_error_passes_through():
    from fundus.client.cli import _failure_message

    msg = _failure_message("http://h/mcp", True, Exception("Connection refused"))
    assert "Connection refused" in msg and "failed" in msg


def test_client_version():
    from typer.testing import CliRunner

    from fundus.client.cli import client_app

    result = CliRunner().invoke(client_app, ["version"])
    assert result.exit_code == 0
    assert result.output.strip() == "v0.1.0"
