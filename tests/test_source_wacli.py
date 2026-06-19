import sqlite3

from fundus.sources.wacli import WacliSource, to_datetime


def _make_db(path):
    con = sqlite3.connect(path)
    con.execute(
        "CREATE TABLE messages (msg_id TEXT, chat_jid TEXT, chat_name TEXT, ts INTEGER, "
        "sender_name TEXT, text TEXT, media_caption TEXT, display_text TEXT)"
    )
    rows = [
        ("1", "chatA", "Alice", 1700000000, "Alice", "hello", None, None),
        ("2", "chatA", "Alice", 1700000060, "Me", "hi back", None, None),
        ("3", "chatA", "Alice", 1700000120, "Alice", None, "photo caption", None),
        ("4", "chatA", "Alice", 1700000180, "Alice", None, None, None),  # empty -> skipped
        ("5", "chatB", "Bob", 1700000200, "Bob", "yo", None, None),
    ]
    con.executemany("INSERT INTO messages VALUES (?,?,?,?,?,?,?,?)", rows)
    con.commit()
    con.close()


def _src(path):
    return WacliSource("chat", db=str(path), connect=lambda: sqlite3.connect(f"file:{path}?mode=ro", uri=True))


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
