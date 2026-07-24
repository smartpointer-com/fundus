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


_SHOW_WITH_ATTACHMENT = [
    [
        [
            {
                "id": "m1",
                "timestamp": 1700000000,
                "tags": ["inbox"],
                "headers": {"Subject": "Invoice", "From": "biller@x"},
                "body": [
                    {
                        "id": 1,
                        "content-type": "multipart/mixed",
                        "content": [
                            {"id": 2, "content-type": "text/plain", "content": "see attached"},
                            {
                                "id": 3,
                                "content-type": "application/pdf",
                                "filename": "invoice.pdf",
                                "content-disposition": "attachment",
                            },
                            {"id": 4, "content-type": "image/png", "filename": "logo.png"},
                        ],
                    }
                ],
            },
            [],
        ]
    ]
]


def _attach_runner(args):
    if args[0] == "count":
        return "1\tuuid\t9\n"
    if args[0] == "show":
        return json.dumps(_SHOW_WITH_ATTACHMENT)
    return ""


def test_notmuch_emits_document_attachment_items():
    seen = {}

    def part_runner(message_id, part_id):
        seen["call"] = (message_id, part_id)
        return b"%PDF-1.6 fake"

    src = NotmuchSource("mail", runner=_attach_runner, part_runner=part_runner)
    items = list(src.changed(None))
    # email body + the PDF attachment (as a file); the inline image is skipped
    assert [i.item_kind for i in items] == ["email", "file"]
    att = items[1]
    assert seen["call"] == ("m1", 3)
    assert att.native_id == "m1#3"
    assert att.title == "invoice.pdf"
    assert att.mime_type == "application/pdf"
    assert att.payload.data == b"%PDF-1.6 fake"
    assert "attachment" in att.tags
    assert att.ts == items[0].ts and att.actors == items[0].actors


def test_notmuch_attachments_can_be_disabled():
    def part_runner(message_id, part_id):
        raise AssertionError("should not fetch parts when disabled")

    src = NotmuchSource("mail", runner=_attach_runner, part_runner=part_runner, attachments=False)
    assert [i.item_kind for i in src.changed(None)] == ["email"]


def test_notmuch_attachment_fetch_failure_is_skipped():
    def part_runner(message_id, part_id):
        raise RuntimeError("notmuch part read failed")

    src = NotmuchSource("mail", runner=_attach_runner, part_runner=part_runner)
    # the email still indexes; the unreadable attachment is dropped, not fatal
    assert [i.item_kind for i in src.changed(None)] == ["email"]


def test_notmuch_live_ids_messages_only_when_attachments_off():
    # cheap search path: just message ids
    src = NotmuchSource("mail", runner=_runner, attachments=False)
    assert list(src.live_ids()) == ["m1", "m2"]


def test_notmuch_live_ids_includes_attachment_part_ids():
    # attachments on: live_ids must enumerate <msgid>#<part> so a --full reconcile keeps them
    src = NotmuchSource("mail", runner=_attach_runner)
    assert list(src.live_ids()) == ["m1", "m1#3"]  # message + its PDF part; inline image skipped


def test_message_body_html_fallback():
    msg = {"body": [{"content-type": "text/html", "content": "<p>Hi <b>there</b></p>"}]}
    assert "there" in message_body(msg)


def test_iter_messages_flattens_threads():
    assert [m["id"] for m in _iter_messages(_SHOW)] == ["m1"]


def test_default_run_decodes_non_utf8_leniently(monkeypatch):
    class FakeCompleted:
        stdout = b'{"body": "caf\xfc"}'  # 0xfc is a Latin-1 byte, invalid UTF-8

    monkeypatch.setattr("fundus.sources.notmuch.subprocess.run", lambda *a, **k: FakeCompleted())
    out = NotmuchSource("mail")._default_run(["show"])
    assert json.loads(out)["body"] == "caf�"  # bad byte -> replacement char, JSON still valid


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
