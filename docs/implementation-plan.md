# Fundus — Implementation Plan

Phased build. Each phase is independently testable and leaves the tree in a
working state. The early phases prioritize **extraction quality**, since that is
the dominant lever on the final index.

## Phase 0 — Scaffold & foundations  *(this commit)*

- Repository layout, packaging (`pyproject.toml`), `Makefile`, tooling
  (`ruff`, `mypy`, `pytest`).
- Domain models (`models.py`) and the core interfaces (`Source`, `Extractor`,
  `ExtractionCache`, `Chunker`, `Sink`, `StateStore`).
- Configuration model + loader; CLI command surface (stubs).
- `docker/compose.yml` for the service stack.

## Phase 1 — Extraction spine & bake-off

- `Extractor` adapters for **docling-serve** and **Apache Tika** (REST), behind
  the common interface.
- Content-addressed extraction cache (SQLite + blobs), keyed by
  `hash(bytes) + engine + version + options`.
- `bakeoff/runner.py`: run the engines over a sample, emit each engine's Markdown
  plus a report (timing, page/table counts, OCR used); optional LLM judge.
- **Outcome:** choose the default extraction engine empirically on the target
  corpus before building the rest.

## Phase 2 — Chunking

- `chunk/text.py` (structure-aware + size-window), `chunk/chat.py` (conversation
  windows), `chunk/tabular.py` (per-sheet/row-group), and `chunk/dispatch.py`.
- Token budgeting via `tokenizers`.

## Phase 3 — Index sink

- `index/meili.py` (`Sink`): apply settings (searchable/filterable/sortable,
  `localizedAttributes`, REST embedder), batched upsert, deterministic ids.
- `index/query.py`: hybrid query shapes + parent grouping.

## Phase 4 — Sources

- `files` connector first (exercises extraction), then `notmuch` (via
  `notmuch show --format=json`), then `wacli` (read-only SQLite).
- Per-source cursors + `StateStore`; incremental traversal.

## Phase 5 — Incremental & deletions

- `core/reconcile.py` (set-difference deletion on `--full`).
- CLI polish: `--only`, `--full`, `--force`, exit-early; global lock.

## Phase 6 — Query & serve

- `serve/mcp.py`: MCP server exposing read-only search with the search key.

## Phase 7 — Packaging & ops

- Finalize `docker/compose.yml`, example config, and operator docs; scheduling
  guidance for periodic incremental runs and a periodic full reconcile.

## Deferred / open items

- **License** choice.
- **Distribution name** availability check before any publish (the bare name
  `fundus` is used by an unrelated package on at least one index).
- **Reranker** (client-side, after hybrid retrieval).
- **Media transcription** (voice notes / image captioning) as additional text.
- **Semantic chat segmentation** (embedding-based topic boundaries) as an upgrade
  over window heuristics.
- **Embedding model size** + binary-quantization threshold, decided alongside the
  bake-off.
- **MinerU** adapter, if the bake-off shows it materially beats the default on the
  target corpus.
