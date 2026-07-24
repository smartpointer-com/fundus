from datetime import UTC, datetime

from fundus.chunk.chat import ChatChunker
from fundus.chunk.dispatch import chunker_for, is_tabular
from fundus.chunk.tabular import TabularChunker
from fundus.chunk.text import TextChunker
from fundus.models import BlobPayload, SourceItem, TextPayload


def _item(kind, mime=None, payload=None):
    return SourceItem(
        source="s",
        type="t",
        native_id="n",
        item_kind=kind,
        mime_type=mime,
        ts=datetime(2024, 1, 1, tzinfo=UTC),
        payload=payload or TextPayload(text="x"),
    )


def test_is_tabular():
    assert is_tabular("text/csv")
    assert not is_tabular("application/pdf")
    assert not is_tabular(None)


def test_dispatch_routes_by_kind_and_mime():
    assert isinstance(chunker_for(_item("file", "text/csv", BlobPayload())), TabularChunker)
    assert isinstance(chunker_for(_item("file", "application/pdf")), TextChunker)
    assert isinstance(chunker_for(_item("email")), TextChunker)
    assert isinstance(chunker_for(_item("chat_window")), ChatChunker)


def test_parse_sheets_csv_mislabeled_as_excel():
    from fundus.chunk.tabular import parse_sheets

    data = b"Account,Symbol,Qty\nABC-1,AAPL,10\nABC-2,MSFT,5\n"
    rows = parse_sheets(data, "application/vnd.ms-excel")[0][1]
    assert rows[0] == ["Account", "Symbol", "Qty"] and rows[1] == ["ABC-1", "AAPL", "10"]


def test_parse_sheets_strips_utf8_bom():
    from fundus.chunk.tabular import parse_sheets

    data = "﻿Name,Value\nx,1\n".encode()
    assert parse_sheets(data, "application/vnd.ms-excel")[0][1][0] == ["Name", "Value"]


def test_is_binary_spreadsheet_magic():
    from fundus.chunk.tabular import _is_binary_spreadsheet

    assert _is_binary_spreadsheet(b"PK\x03\x04zipdata")  # xlsx/ods
    assert _is_binary_spreadsheet(b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1xls")  # ole2 .xls
    assert not _is_binary_spreadsheet(b"Account,Symbol\n1,2\n")  # plain csv text


def test_parse_sheets_normalizes_cr_line_endings():
    from fundus.chunk.tabular import parse_sheets

    rows = parse_sheets(b"a,b\rA,B\rC,D\r", "text/csv")[0][1]  # old-Mac CR endings
    assert rows[0] == ["a", "b"] and rows[1] == ["A", "B"] and rows[2] == ["C", "D"]
