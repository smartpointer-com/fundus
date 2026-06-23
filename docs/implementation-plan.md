# Fundus — Implementation Plan

Phased build. Each phase is independently testable and leaves the tree working,
with a unit-test suite and `ruff` + `mypy --strict` clean.

## Status

All phases are **implemented and tested**, and the pipeline has been validated
against live services on a real corpus. The bake-off favoured an escalating router
(tika-first, docling for scans) as the default. Capabilities added beyond the
original phases: fan-out embedding with a reusable vector cache, the escalating
extraction router with a sequential-retry fallback for big scans, and indexing of
email/WhatsApp document attachments.

| Phase | Status |
|---|---|
| 0 — Scaffold & foundations | ✅ |
| 1 — Extraction spine & bake-off | ✅ |
| 2 — Chunking | ✅ |
| 3 — Index sink | ✅ |
| 4 — Sources | ✅ |
| 5 — Pipeline, state, reconcile, CLI, locking | ✅ |
| 6 — Query & MCP serve | ✅ |
| 7 — Packaging & ops | ✅ |

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
- **Paths**: the config file follows `XDG_CONFIG_HOME` (`~/.config/fundus.toml`),
  but all runtime data is consolidated under one configurable root
  (`[storage].data_dir`, default `$XDG_DATA_HOME/fundus`): `meili/`, `cache/`,
  `state/`. `fundus paths` reports the resolved locations.

## Phases (reference)

- **0 — Scaffold:** layout, packaging, tooling, domain models, interfaces.
- **1 — Extraction spine:** `docling-serve` + `tika` adapters, content-addressed
  cache, Markdown/text→Block normalizer, bake-off runner.
- **2 — Chunking:** structure-aware text, chat windows, tabular; dispatch.
- **3 — Index sink:** Meili settings, REST embedder, batched upsert,
  reconciliation (set-difference deletion + rsync-style file-tree content diff),
  hybrid query grouped by parent.
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
