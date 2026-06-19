from fundus.core.state import JsonStateStore


def test_state_roundtrip_and_persist(tmp_path):
    path = tmp_path / "sub" / "cursors.json"
    store = JsonStateStore(path)
    assert store.get_cursor("mail") is None
    store.set_cursor("mail", "42")
    store.set_cursor("chat", "7")
    assert store.get_cursor("mail") == "42"
    assert JsonStateStore(path).get_cursor("chat") == "7"  # persisted across instances
