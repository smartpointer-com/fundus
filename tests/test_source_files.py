import os
from pathlib import Path

from fundus.sources.files import FilesSource


def test_files_changed_and_cursor(tmp_path):
    (tmp_path / "a.txt").write_text("hello")
    sub = tmp_path / "sub"
    sub.mkdir()
    (sub / "b.txt").write_text("world")
    src = FilesSource("docs", roots=[str(tmp_path)])
    items = list(src.changed(None))
    ids = {i.native_id for i in items}
    assert str(tmp_path / "a.txt") in ids
    assert str(sub / "b.txt") in ids
    for item in items:
        assert item.item_kind == "file"
        assert item.payload.data
        assert item.mime_type
    cursor = src.current_cursor()
    assert float(cursor) > 0
    assert list(src.changed(cursor)) == []  # nothing newer


def test_files_incremental(tmp_path):
    f = tmp_path / "a.txt"
    f.write_text("v1")
    src = FilesSource("docs", roots=[str(tmp_path)])
    list(src.changed(None))
    cursor = src.current_cursor()
    future = float(cursor) + 10
    os.utime(f, (future, future))
    assert [i.native_id for i in src.changed(cursor)] == [str(f)]


def test_files_live_ids_includes_empty_but_changed_skips_it(tmp_path):
    (tmp_path / "a.txt").write_text("x")
    (tmp_path / "empty.txt").write_text("")
    src = FilesSource("docs", roots=[str(tmp_path)])
    changed = list(src.changed(None))
    assert all(Path(i.native_id).name != "empty.txt" for i in changed)
    assert set(src.live_ids()) >= {str(tmp_path / "a.txt"), str(tmp_path / "empty.txt")}
