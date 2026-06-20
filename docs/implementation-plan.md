# Fundus — Implementation Plan

Phased build. Each phase is independently testable and leaves the tree working,
with a unit-test suite and `ruff` + `mypy --strict` clean.

## Status

Phases 0–6 are **implemented and tested**. Phase 7 (packaging/ops/docs) is in
progress. Integration against live services (Meilisearch, docling-serve/Tika, the
embedding endpoint) is the remaining real-world validation; the bake-off chooses
the default extraction engine on a real corpus sample.

| Phase | Status |
|---|---|
| 0 — Scaffold & foundations | ✅ |
| 1 — Extraction spine & bake-off | ✅ |
| 2 — Chunking | ✅ |
| 3 — Index sink | ✅ |
| 4 — Sources | ✅ |
| 5 — Pipeline, state, reconcile, CLI, locking | ✅ |
| 6 — Query & MCP serve | ✅ |
| 7 — Packaging & ops | 🚧 |

## Notable design decisions made during implementation

- **Chat windowing** lives in the `ChatChunker`, which reads a chat's messages
  from `SourceItem.extra["messages"]` (the wacli source prepares that per-chat
  batch). Each window's message ids, participants, and time span ride in
  `Chunk.meta`.
- **Text files bypass the extraction engine.** `text/*` files (and Markdown/HTML)
  are read natively; only binary documents (PDF/Office) go to docling/Tika.
  Tabular files (`text/csv`, spreadsheets) go straight to the tabular chunker.
- **`locales`** is a single top-level config setting driving both the index's
  `localizedAttributes` and the default OCR languages.
- **Paths** follow XDG: config at `~/.config/fundus.toml`, cursors under
  `$XDG_STATE_HOME/fundus/`, extraction cache under `$XDG_CACHE_HOME/fundus/`.

## Phases (reference)

- **0 — Scaffold:** layout, packaging, tooling, domain models, interfaces.
- **1 — Extraction spine:** `docling-serve` + `tika` adapters, content-addressed
  cache, Markdown/text→Block normalizer, bake-off runner.
- **2 — Chunking:** structure-aware text, chat windows, tabular; dispatch.
- **3 — Index sink:** Meili settings, REST embedder, batched upsert,
  set-difference deletion, hybrid query grouped by parent.
- **4 — Sources:** files, notmuch (CLI JSON), wacli (configurable schema); registry.
- **5 — Pipeline:** routing + chunking + upsert, per-source cursors, `--full`
  reconcile, run lock, config loader, CLI.
- **6 — Serve:** MCP `search` tool over the read-only key.
- **7 — Packaging & ops:** CI, type marker, docs; deployment guidance.

## Deferred / open items

- **License** choice and **distribution name** availability check before any
  publish (the bare name `fundus` is used by an unrelated package on at least one
  index).
- **Integration tests** against live Meilisearch + an extraction engine
  (testcontainers); verify the docling-serve request field names against a running
  instance.
- **Reranker** (client-side, after hybrid retrieval).
- **Media transcription** (voice notes / image captioning) as additional text.
- **Semantic chat segmentation** (embedding-based topic boundaries) over the
  window heuristics.
- **Accurate token counting** via the optional `tokenize` extra (a real tokenizer
  instead of the chars/4 heuristic).
- **MinerU** adapter, if the bake-off shows it materially beats the default.
