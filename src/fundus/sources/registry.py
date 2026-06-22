"""Build source connectors from configuration."""

from __future__ import annotations

from typing import Any

from fundus.config import SourceConfig
from fundus.sources.base import Source
from fundus.sources.files import FilesSource
from fundus.sources.notmuch import NotmuchSource
from fundus.sources.wacli import WacliSource

_WACLI_KEYS = {
    "table",
    "id_col",
    "chat_col",
    "chat_name_col",
    "ts_col",
    "sender_col",
    "text_cols",
    "where",
    "media",
    "media_type_col",
    "filename_col",
    "mime_col",
    "path_col",
    "document_media_type",
    "media_dir",
}


def build_source(cfg: SourceConfig) -> Source:
    opts: dict[str, Any] = cfg.model_dump(exclude={"name", "type"})
    if cfg.type == "files":
        return FilesSource(cfg.name, roots=opts["roots"], max_bytes=opts.get("max_bytes"))
    if cfg.type == "notmuch":
        return NotmuchSource(
            cfg.name,
            db=opts.get("db"),
            query=opts.get("query", "*"),
            attachments=opts.get("attachments", True),
        )
    if cfg.type == "wacli":
        kwargs = {k: v for k, v in opts.items() if k in _WACLI_KEYS}
        return WacliSource(cfg.name, db=opts["db"], **kwargs)
    raise KeyError(f"unknown source type: {cfg.type!r}")
