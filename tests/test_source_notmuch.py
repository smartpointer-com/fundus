import json

import pytest

from fundus.sources.notmuch import NotmuchSource, _iter_messages, message_body

_SHOW = [
    [
        [
            {
                "id": "m1",
                "timestamp": 1700000000,
                "tags": ["inbox", "unread"],
                "headers": {"Subject": "Hi", "From": "a@x", "To": "b@y"},
                "body": [{"content-type": "text/plain", "content": "Hello body"}],
            },
            [],
        ]
    ]
]


def _runner(args):
    if args[0] == "count":
        return "5\tuuid\t42\n"
    if args[0] == "show":
        return json.dumps(_SHOW)
    if args[0] == "search":
        return "id:m1\nid:m2\n"
    return ""


def test_notmuch_changed():
    src = NotmuchSource("mail", query="*", runner=_runner)
    items = list(src.changed(None))
    assert len(items) == 1
    item = items[0]
    assert item.native_id == "m1"
    assert item.item_kind == "email"
    assert item.title == "Hi"
    assert "Hello body" in item.payload.text
    assert set(item.tags) == {"inbox", "unread"}
    assert "a@x" in item.actors
    assert src.current_cursor() == "42"


def test_notmuch_live_ids():
    assert list(NotmuchSource("mail", runner=_runner).live_ids()) == ["m1", "m2"]


def test_message_body_html_fallback():
    msg = {"body": [{"content-type": "text/html", "content": "<p>Hi <b>there</b></p>"}]}
    assert "there" in message_body(msg)


def test_iter_messages_flattens_threads():
    assert [m["id"] for m in _iter_messages(_SHOW)] == ["m1"]


@pytest.mark.parametrize("query", ["*", "tag:inbox"])
def test_changed_builds_lastmod_query(query):
    captured = {}

    def runner(args):
        if args[0] == "show":
            captured["query"] = args[-1]
        return "5\tuuid\t7\n" if args[0] == "count" else "[]"

    list(NotmuchSource("mail", query=query, runner=runner).changed("3"))
    assert "lastmod:3.." in captured["query"]
    assert query in captured["query"]
