import pytest

from fundus.config import SourceConfig
from fundus.sources.files import FilesSource
from fundus.sources.notmuch import NotmuchSource
from fundus.sources.registry import build_source
from fundus.sources.wacli import WacliSource


def test_build_files():
    src = build_source(SourceConfig(name="d", type="files", roots=["/tmp"]))
    assert isinstance(src, FilesSource) and src.name == "d"


def test_build_notmuch():
    assert isinstance(build_source(SourceConfig(name="m", type="notmuch", query="tag:inbox")), NotmuchSource)


def test_build_wacli_with_schema_override():
    src = build_source(SourceConfig(name="c", type="wacli", db="/x.db", sender_col="sender_jid"))
    assert isinstance(src, WacliSource)
    assert src._sender == "sender_jid"


def test_unknown_type():
    with pytest.raises(KeyError):
        build_source(SourceConfig(name="x", type="bogus"))
