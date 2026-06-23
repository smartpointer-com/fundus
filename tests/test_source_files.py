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


def test_files_fingerprints_are_stat_only_and_skip_empty(tmp_path):
    f = tmp_path / "a.txt"
    f.write_text("hello")
    (tmp_path / "empty.txt").write_text("")  # zero-byte: not indexable, excluded from the manifest
    src = FilesSource("docs", roots=[str(tmp_path)])
    fps = dict(src.fingerprints())
    assert set(fps) == {str(f)}
    st = f.stat()
    assert fps[str(f)].size == st.st_size
    assert fps[str(f)].mtime == st.st_mtime
    assert float(src.current_cursor()) >= st.st_mtime  # walk advanced the watermark


def test_files_load_reads_one_file(tmp_path):
    f = tmp_path / "a.txt"
    f.write_text("hello")
    src = FilesSource("docs", roots=[str(tmp_path)])
    item = src.load(str(f))
    assert item is not None and item.payload.data == b"hello"
    assert item.size == f.stat().st_size and item.mtime == f.stat().st_mtime
    assert src.load(str(tmp_path / "missing.txt")) is None  # absent path -> None


def test_files_fingerprint_catches_mtime_regression_that_changed_skips(tmp_path):
    # The exact false-negative --full must fix: a file whose mtime moves BACKWARDS below the
    # cursor is invisible to changed(), but its fingerprint still differs from a stale manifest.
    f = tmp_path / "a.txt"
    f.write_text("v1")
    src = FilesSource("docs", roots=[str(tmp_path)])
    list(src.changed(None))
    cursor = src.current_cursor()
    f.write_text("v2 longer content")
    past = float(cursor) - 100  # synced with an older mtime than the watermark
    os.utime(f, (past, past))
    assert list(src.changed(cursor)) == []  # cursor blind to it
    assert dict(src.fingerprints())[str(f)].mtime == past  # manifest diff would still catch it
