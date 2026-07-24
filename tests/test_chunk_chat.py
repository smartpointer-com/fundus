from datetime import UTC, datetime, timedelta

from fundus.chunk.chat import (
    ChatChunker,
    ChatMessage,
    ChatWindowParams,
    transcript,
    window_messages,
)
from fundus.models import SourceItem, TextPayload


def _msgs(n, gap_seconds=60, text="word word word word"):
    base = datetime(2024, 1, 1, 12, 0, 0, tzinfo=UTC)
    return [
        ChatMessage(id=str(i), ts=base + timedelta(seconds=i * gap_seconds), sender=f"u{i % 2}", text=text)
        for i in range(n)
    ]


def test_single_window_when_small():
    windows = window_messages(_msgs(3), ChatWindowParams())
    assert len(windows) == 1
    assert [m.id for m in windows[0]] == ["0", "1", "2"]


def test_splits_on_budget_with_overlap():
    params = ChatWindowParams(target_tokens=10, max_tokens=30, overlap_messages=1, soft_gap_hours=999)
    windows = window_messages(_msgs(12), params)
    assert len(windows) >= 2
    assert windows[0][-1].id == windows[1][0].id  # 1-message overlap


def test_long_gap_splits_after_target():
    params = ChatWindowParams(target_tokens=5, max_tokens=1000, overlap_messages=0, soft_gap_hours=1)
    base = datetime(2024, 1, 1, 12, 0, 0, tzinfo=UTC)
    msgs = [
        ChatMessage(id="0", ts=base, sender="a", text="hello world"),
        ChatMessage(id="1", ts=base + timedelta(minutes=1), sender="a", text="more text"),
        ChatMessage(id="2", ts=base + timedelta(hours=5), sender="b", text="next day topic"),
    ]
    windows = window_messages(msgs, params)
    assert len(windows) == 2
    assert [m.id for m in windows[1]] == ["2"]


def test_short_gaps_do_not_split():
    params = ChatWindowParams(target_tokens=1, max_tokens=1000, overlap_messages=0, soft_gap_hours=6)
    windows = window_messages(_msgs(5, gap_seconds=600), params)  # 10-minute gaps
    assert len(windows) == 1


def test_transcript_render():
    msgs = [
        ChatMessage(id="0", ts=datetime(2024, 1, 1, tzinfo=UTC), sender="a", text="hi"),
        ChatMessage(id="1", ts=datetime(2024, 1, 1, tzinfo=UTC), sender="", text="system"),
    ]
    assert transcript(msgs) == "a: hi\nsystem"


def test_chat_chunker_reads_item_extra():
    msgs = [{"id": str(i), "ts": "2024-01-01T12:00:00", "sender": "a", "text": "hi"} for i in range(3)]
    item = SourceItem(
        source="chat",
        type="wacli",
        native_id="jid1",
        item_kind="chat_window",
        ts=datetime(2024, 1, 1, tzinfo=UTC),
        payload=TextPayload(text=""),
        extra={"messages": msgs},
    )
    chunks = ChatChunker().chunk(None, item)
    assert len(chunks) == 1
    assert chunks[0].meta["msg_ids"] == ["0", "1", "2"]
    assert chunks[0].meta["actors"] == ["a"]
