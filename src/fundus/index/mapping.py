"""Map a (SourceItem, Chunk) pair to the IndexDocument stored in the search index."""

from __future__ import annotations

from fundus.core.ids import doc_id
from fundus.models import Chunk, IndexDocument, SourceItem


def to_index_document(
    item: SourceItem, chunk: Chunk, *, extract_sig: str = "", ocr_used: bool = False
) -> IndexDocument:
    meta = chunk.meta
    ts = meta["ts"] if isinstance(meta.get("ts"), int) else int(item.ts.timestamp())
    actors = meta["actors"] if isinstance(meta.get("actors"), list) else item.actors
    msg_ids = meta["msg_ids"] if isinstance(meta.get("msg_ids"), list) else None
    lang = item.extra.get("lang")

    body = chunk.text
    if chunk.heading_path:
        body = " > ".join(chunk.heading_path) + "\n\n" + chunk.text

    return IndexDocument(
        id=doc_id(item.source, item.native_id, chunk.seq),
        source=item.source,
        item_kind=item.item_kind,
        parent_id=chunk.parent_id,
        native_id=item.native_id,
        title=item.title,
        body=body,
        ts=ts,
        actors=actors,
        tags=item.tags,
        path=item.path,
        mime=item.mime_type,
        lang=lang if isinstance(lang, list) else [],
        msg_ids=msg_ids,
        size=item.size or 0,
        mtime=item.mtime or 0.0,
        extract_sig=extract_sig,
        ocr_used=ocr_used,
    )
