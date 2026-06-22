import sqlite3

from fundus.sources.wacli import WacliSource, to_datetime


def _make_db(path):
    con = sqlite3.connect(path)
    con.execute(
        "CREATE TABLE messages (msg_id TEXT, chat_jid TEXT, chat_name TEXT, ts INTEGER, "
        "sender_name TEXT, text TEXT, media_caption TEXT, display_text TEXT, "
        "media_type TEXT, filename TEXT, mime_type TEXT, local_path TEXT)"
    )
    n = (None, None, None, None)  # media_type, filename, mime_type, local_path
    rows = [
        ("1", "chatA", "Alice", 1700000000, "Alice", "hello", None, None, *n),
        ("2", "chatA", "Alice", 1700000060, "Me", "hi back", None, None, *n),
        ("3", "chatA", "Alice", 1700000120, "Alice", None, "photo caption", None, *n),
        ("4", "chatA", "Alice", 1700000180, "Alice", None, None, None, *n),  # empty -> skipped
        ("5", "chatB", "Bob", 1700000200, "Bob", "yo", None, None, *n),
    ]
    con.executemany("INSERT INTO messages VALUES (?,?,?,?,?,?,?,?,?,?,?,?)", rows)
    con.commit()
    con.close()


def _add_message(path, values):
    con = sqlite3.connect(path)
    con.execute("INSERT INTO messages VALUES (?,?,?,?,?,?,?,?,?,?,?,?)", values)
    con.commit()
    con.close()


def _src(path, **kw):
    return WacliSource(
        "chat", db=str(path),
        connect=lambda: sqlite3.connect(f"file:{path}?mode=ro", uri=True), **kw,
    )


def test_wacli_groups_by_chat_and_falls_back_to_caption(tmp_path):
    db = tmp_path / "w.db"
    _make_db(db)
    items = {i.native_id: i for i in _src(db).changed(None)}
    assert set(items) == {"chatA", "chatB"}
    chat_a = items["chatA"]
    assert chat_a.item_kind == "chat_window"
    msgs = chat_a.extra["messages"]
    assert [m["id"] for m in msgs] == ["1", "2", "3"]  # empty message 4 skipped
    assert msgs[2]["text"] == "photo caption"  # caption fallback
    assert chat_a.title == "Alice"
    assert "Alice" in chat_a.actors


def test_wacli_incremental(tmp_path):
    db = tmp_path / "w.db"
    _make_db(db)
    src = _src(db)
    items = list(src.changed("1700000190"))  # only chatB (ts 200) is newer
    assert {i.native_id for i in items} == {"chatB"}
    assert src.current_cursor() == "1700000200"


def test_wacli_live_ids(tmp_path):
    db = tmp_path / "w.db"
    _make_db(db)
    assert set(_src(db).live_ids()) == {"chatA", "chatB"}


def test_to_datetime_units():
    assert to_datetime(1700000000).year == 2023
    assert to_datetime(1700000000000).year == 2023  # milliseconds
    assert to_datetime("2024-01-01T00:00:00").year == 2024


def test_wacli_emits_document_media_items(tmp_path):
    db = tmp_path / "w.db"
    _make_db(db)
    media_file = tmp_path / "media" / "chatA" / "9" / "document" / "invoice.pdf"
    media_file.parent.mkdir(parents=True)
    media_file.write_bytes(b"%PDF-1.7 fake")
    # stale absolute base in local_path -> must be reconstructed against the current media dir
    _add_message(db, ("9", "chatA", "Alice", 1700000300, "Alice", None, "here's the doc", None,
                      "document", "invoice.pdf", "application/pdf",
                      "/OLD/STORE/media/chatA/9/document/invoice.pdf"))
    # an image share must NOT be emitted as a file (documents only)
    _add_message(db, ("10", "chatA", "Alice", 1700000360, "Alice", None, None, None,
                      "image", "photo.jpg", "image/jpeg", "/x/media/chatA/10/image/photo.jpg"))
    files = [i for i in _src(db).changed(None) if i.item_kind == "file"]
    assert len(files) == 1
    f = files[0]
    assert f.native_id == "9" and f.title == "invoice.pdf" and f.mime_type == "application/pdf"
    assert f.payload.path == str(media_file)  # reconstructed to the current media dir
    assert "whatsapp" in f.tags and "attachment" in f.tags


def test_wacli_skips_media_when_file_missing(tmp_path):
    db = tmp_path / "w.db"
    _make_db(db)
    _add_message(db, ("9", "chatA", "Alice", 1700000300, "Alice", None, None, None,
                      "document", "gone.pdf", "application/pdf",
                      "/x/media/chatA/9/document/gone.pdf"))  # no such file on disk
    assert all(i.item_kind != "file" for i in _src(db).changed(None))


def test_wacli_skips_media_without_local_path(tmp_path):
    db = tmp_path / "w.db"
    _make_db(db)
    _add_message(db, ("9", "chatA", "Alice", 1700000300, "Alice", None, None, None,
                      "document", "undownloaded.pdf", "application/pdf", None))
    assert all(i.item_kind != "file" for i in _src(db).changed(None))


def test_wacli_media_can_be_disabled(tmp_path):
    db = tmp_path / "w.db"
    _make_db(db)
    _add_message(db, ("9", "chatA", "Alice", 1700000300, "Alice", None, None, None,
                      "document", "x.pdf", "application/pdf", "/p/x.pdf"))
    assert all(i.item_kind != "file" for i in _src(db, media=False).changed(None))
