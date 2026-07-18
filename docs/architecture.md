# Fundus — Architecture

Fundus indexes a heterogeneous corpus — email, chat messages, and document files —
into **Meilisearch**, providing full-text and hybrid (keyword + semantic) search
for agents. It is organized around two plugin families — **sources** and
**extraction engines** — both swappable by configuration.

## Design goals

- **Pluggable, configurable sources.** Nothing is hard-coded to a particular
  filesystem layout; all corpus locations come from config.
- **Engine-agnostic extraction.** Swap or A/B-test extraction engines without
  touching the rest of the pipeline.
- **Incremental indexing** with explicit deletion reconciliation.
- **Writer/reader split.** An indexer holds write access; consumers get
  read-only search access.
- **Right component, right place.** The search engine and extraction engines run
  in Docker; the embedding model runs bare-metal and is reached over HTTP.

## Process topology

```
  ┌─────────────────────── Indexer host (writer) ────────────────────────┐
  │  fundus  (Python CLI / orchestrator) — a thin client; holds NO models │
  │     reads the configured source locations                             │
  │        │ HTTP            │ HTTP              │ subprocess / sqlite     │
  │        ▼                 ▼                   ▼                         │
  │  Meilisearch       extraction engine(s)   notmuch CLI / wacli sqlite  │
  │  (Docker)          (Docker: docling/tika)                             │
  │        │ HTTP (fan-out: Fundus embeds documents + queries,           │
  │        ▼         then stores the vectors in Meilisearch)             │
  │  Embedding model server (BARE METAL, OpenAI-compatible /v1/embeddings)│
  │                                                                       │
  │  fundus serve ──▶ MCP server (read-only search key)                   │
  └───────────────────────────────────────────────────────────────────────┘
            ▲ read-only search key (no admin, no write)
            │
     Agent / consumer  (may run as a separate, least-privileged user)
```

## Repository layout

```
src/fundus/
  cli.py            Typer app: init | index | query | connect | serve | sources | paths | service | embed-backfill | bakeoff
  service/          launchd job generation + management (index jobs + the MCP serve daemon)
  config.py         configuration model + loader (TOML + env)
  models.py         domain models (SourceItem, ExtractionResult/Block, Chunk, IndexDocument)
  render.py         shared search-result rendering (used by `query` and fundus-client)
  core/             pipeline, parallel worker pool, state (cursors), ids, reconcile (deletions)
  sources/          base.Source + notmuch / wacli / files connectors + registry
  extract/          base.Extractor + docling / tika adapters + escalating router + cache + registry
  chunk/            base.Chunker + text / chat / tabular + dispatch
  index/            base.Sink + meili impl + settings + query
  embed/            embedder config (REST or fan-out userProvided) + fan-out client + vector cache
  serve/            read-only MCP server (search/sources/locate tools) + bearer-token gate
  client/           fundus-client: thin MCP client for humans/scripts (a second console script)
  bakeoff/          extraction-engine comparison harness
docker/compose.yml  Meilisearch + extraction engines
config/fundus.example.toml
docs/
```

## Core interfaces

| Interface | File | Responsibility |
|---|---|---|
| `Source` | `sources/base.py` | Enumerate a corpus incrementally; list live ids |
| `Extractor` | `extract/base.py` | One adapter per engine; bytes → normalized result |
| `ExtractionCache` | `extract/cache.py` | Content-addressed cache, keyed per engine+version+options |
| `Chunker` | `chunk/base.py` | Normalized result → chunks |
| `Sink` | `index/base.py` | Schema, batched upsert, deletion |
| `StateStore` | `core/state.py` | Per-source cursors (atomic JSON + lock) |

## Domain models

The pipeline passes a small set of typed records (`models.py`):

- **`SourceItem`** — one logical artifact (email, chat window, file). Its
  `payload` is either a `TextPayload` (already textual — bypasses extraction) or
  a `BlobPayload` (binary document — goes through an engine). Connectors also emit
  **document attachments** as their own `file` items — email parts (notmuch) and
  documents shared in WhatsApp (wacli) — so they're searchable like any file.
- **`ExtractionResult`** — engine-agnostic output: an ordered list of `Block`s
  (heading/paragraph/table/…), a Markdown serialization, and metadata. Rich
  engines populate structure; flat engines degrade to a paragraph stream.
- **`Chunk`** — the indexing unit, linked to its artifact by `parent_id`.
- **`IndexDocument`** — the record stored in the search index.

## Indexing pipeline

`core/pipeline.py` runs, per enabled source:

```
cursor = state.get_cursor(source.name)
for item in source.changed(cursor):
    result = item.payload as result            # text payloads pass through …
             else extract_with_cache(item)     # … blobs go to an engine (cached)
    chunks = dispatch_chunker(item).chunk(result, item)   # text / chat / tabular
    docs   = [to_index_document(item, c) for c in chunks]
    sink.upsert(docs)                          # batched
state.set_cursor(source.name, source.current_cursor())   # atomic
```

A `--full` run additionally reconciles the index against reality (see
[Incremental indexing & deletions](#incremental-indexing--deletions) below):
deletions for every source, and for **file-tree sources** a full content
reconcile that also re-indexes edits the cursor cannot see.

Flags: `--only <source>`, `--full`, `--force`, plus an exit-early option for
cheap iteration. A global lock prevents concurrent runs.

With **fan-out** indexing the per-source loop runs on a bounded worker pool:
extraction and embedding happen concurrently across items, each chunk's vector is
computed (cache-first) and attached before upsert, and a per-item failure is logged
and skipped rather than aborting the run. A **vector cache** (keyed by model +
embed-input text) lets a re-index reuse embeddings; `embed-backfill` seeds it from
vectors already in the index, making a re-run that changes little nearly free.

## Query & serve

`fundus query` / `fundus serve` build a **hybrid** Meilisearch query
(`semanticRatio` blending keyword + vector) with filters on `source`, `ts`,
`actors`, `tags`, etc., then **group hits by `parent_id`** so the consumer gets
artifacts rather than chunk fragments. Each result carries what's needed to act on
it — a follow-up `ref` (email Message-ID / file path / chat JID), timestamp,
source/kind, relevance score, and a matched snippet; `query` adds `--json` and
`--fields`.

## Read-only access for agents (MCP)

`fundus serve` is the **read-only surface** handed to agents (e.g. OpenClaw). It
exposes three MCP tools — `search` (the hybrid query above), `sources` (what's
indexed and the corpus roots), and `locate` (resolve a hit's `ref` to an openable
path: a file passes through, an email Message-ID resolves to its maildir file via
notmuch). Because the corpus lives in shared directories the agent can read, "find
then open the original" is: `search` → follow the `ref`/`path` → read the file.

Read-only rests on the server only ever exposing read tools (no mutating tool
exists) as a trusted host-side process, gated by:

- **Bearer token.** Over HTTP the server is gated by a token
  (`FUNDUS_SERVE_TOKEN`) checked by a pure-ASGI middleware, so no stray local
  process reaches it. This token (never a Meili key) is what's shared with the agent.
- **Meili search key (optional hardening).** The server prefers a search-scoped key
  (`search_key` / `FUNDUS_MEILI_SEARCH_KEY`) and falls back to the admin key it
  already holds. A separate key only earns its keep once the gateway is split out to
  a less-trusted process; while monolithic, the admin-key fallback is fine.

Transport is **streamable-HTTP** by default (HTTP+SSE was deprecated in the 2025-03
MCP spec; `sse` and `stdio` remain available). OpenClaw consumes it via its native
`openclaw mcp add <name> --url …` with a static bearer header. **`fundus-client`**
is a second console script — a thin MCP client (holding only the endpoint + token,
no Meili keys) that runs the same tools from a shell, for human operators and
scripts.

## Search-index design

A **single index** holds all sources, distinguished by a `source` facet:

- **searchable:** `title`, `body` only
- **filterable:** `source, item_kind, ts, actors, tags, path, mime, lang, parent_id`
- **sortable:** `ts`
- **localizedAttributes:** configure the locales present in your corpus (e.g.
  `["eng", "deu"]`).
- **embedders:** by default a `userProvided` embedder — Fundus computes the
  document vectors itself (fan-out) and stores them; a REST embedder (Meilisearch
  embeds, with a document template over `{{title}} {{body}}`) is the alternative
  for light models.

Index settings are applied **before the first ingest** (changing them later
triggers a full reindex).

> **One index, not one-index-per-language.** Meilisearch's "one index per
> language" guidance targets a *partitionable* catalog and the lexical layer
> only. A personal/organisational corpus has mixed-language documents and is
> queried by an agent in either language, so a single index with hybrid search
> serves it better; declare locales via `localizedAttributes`, and let the vector
> half carry cross-language and compound-word recall. Enable binary quantization
> if the embedder's dimensionality is large and the index grows past ~1M chunks.

## Extraction: engine-agnostic by design

Document text extraction is the dominant quality lever, and the best engine is
corpus-dependent, so it is deliberately swappable:

- Each engine is one `Extractor` adapter (`docling.py`, `tika.py`) hiding its
  transport (REST) behind `extract(req) -> ExtractionResult`.
- An **escalating router** (`router.py`) composes engines: run a cheap one (tika)
  first and fall back to a higher-fidelity one (docling) only when the result is
  too sparse — typically a scanned PDF needing OCR. The docling client bounds its
  concurrency and retries a resource/timeout failure *exclusively* (sequentially),
  so one large scan can have the engine to itself instead of sizing the host for
  many big scans at once.
- All adapters return the **same normalized result**, so the chunker is
  engine-agnostic. Structure-rich engines populate `blocks` and tables; flat
  engines degrade gracefully.
- The **cache key includes the engine name + version + options**, so multiple
  engines' outputs for the same file coexist — enabling honest A/B and ensuring
  re-chunking never re-extracts (never re-OCRs).
- `bakeoff/` runs the engines over a representative sample and reports
  (characters, tables, speed, failures) so the default engine is chosen
  **empirically** on the target corpus.

## Chunking strategy

The indexing unit is a **chunk** (Meilisearch stores one vector per document per
embedder). Granularity differs per item kind, all consuming normalized `blocks`:

- **Documents** — structure-aware chunks on heading/table boundaries with
  overlap, carrying the heading path as context; size-window fallback when no
  structure is present.
- **Chat** — windows of consecutive messages within one conversation, bounded by
  a token budget with overlap; a long silence is a soft cut hint, never a hard
  trigger. Message ids/timestamps are retained so a hit expands back to messages.
- **Spreadsheets / CSV** — per sheet, chunked by row groups with the header row
  repeated in each chunk so it is self-describing.

## Incremental indexing & deletions

The plain `fundus index` is **incremental**. Per-source **cursors** (a notmuch
lastmod, a max rowid/timestamp, an mtime watermark) are persisted in an atomic,
lock-guarded JSON state file and bound *what is read*: `source.changed(cursor)`
yields only items new or modified since the last run, so an unchanged email or
file is never re-read. `--force` ignores the cursor and re-reads the whole corpus.

**No duplicates.** Document ids are content-free and deterministic
(`hash(source, native_id, chunk_seq)`), so when an item *is* re-read — because it
changed, under `--force`, or during a `--full` reconcile — it produces the same
ids and the upsert **overwrites** rather than appends. Combined with the cursor
(unchanged items aren't read at all), indexing is idempotent: the same file or
email is never indexed twice.

`--full` **reconciles the index against reality** — the part a forward-only cursor
structurally cannot do, since a deleted or out-of-band-edited artifact leaves no
record for an incremental walk to observe. Two regimes, by source kind:

- **Append-only stores (mail, chat).** Their artifacts are immutable, so only
  additions (handled by the cursor) and deletions occur. `--full` reconciles
  deletions by **set-difference**: read the `parent_id`s currently indexed for the
  source, subtract the source's currently-live ids (`indexed − live = dead`), and
  remove anything no longer live.
- **File trees (the `files` source).** A file can be edited in place, and its
  mtime is only as trustworthy as whatever wrote it (a Drive/Docs export may sync
  an edit with a *regressed* or unchanged mtime — invisible to the cursor). So
  `--full` does an **rsync-style content reconcile**: read every live file's
  `(size, mtime)` fingerprint, fetch the indexed manifest of the same, and diff.
  New or fingerprint-changed files are re-indexed; files absent from disk are
  deleted. This is independent of the cursor, so an edit is caught regardless of
  what its mtime did. A re-indexed file's old chunks are cleared first, so a file
  that re-chunks into fewer pieces can't leave orphaned chunks behind.

The fingerprint (`size`, `mtime`) is a stored, non-filterable field on each
document — adding it triggers no index-settings change, and documents indexed
before it existed simply read as "absent from the manifest" and get re-indexed
once.

## Service jobs (`fundus service`)

On macOS, `fundus service install` generates and bootstraps up to three launchd
jobs (all by default; scope with `--no-index` / `--no-serve`):

- `<prefix>.index` — incremental indexing (`StartInterval` + `RunAtLoad`).
- `<prefix>.index-full` — the nightly full reconcile (`StartCalendarInterval` at a
  wall-clock hour).
- `<prefix>.serve` — the read-only MCP server, a long-running daemon kept up with
  `KeepAlive` + `RunAtLoad`.

The two index jobs run as throttled **background** batch work (`ProcessType =
Background`, `LowPriorityIO`); the server runs **unthrottled** so it stays
responsive. The run-lock makes the index jobs safe to overlap — an incremental
that fires mid-full simply skips.

- **LaunchAgent (default)** runs in the login session; **`--daemon`** installs a
  LaunchDaemon (via `sudo`) that runs headless at boot — correct only when the
  services it needs (the container stack, the embedder) are themselves up without a
  login. A daemon still runs as the invoking user, not root.
- **Points at the *installed* binary.** `install` refuses to wire up an editable
  dev build (it would break on any source change); run `make install` first, then
  invoke the installed CLI. Secrets are never written into the (world-readable)
  plist — the plist execs `fundus` directly, and `fundus` sources the config's
  `env_file` itself at startup (see Configuration).
- Logs go to `<data_root>/logs/`; `status` (all jobs), `restart` (`--full` /
  `--serve` to target one), and `run` (trigger an index now) round out the
  subcommands. The pure plist generation lives in `service/spec.py`, the
  launchctl/sudo side effects in `service/manager.py`.

## Dependencies

**Runtime:** `typer` (CLI), `pydantic` (models/config), `httpx` (extractor and
embedder calls), `meilisearch` (official client), `selectolax` (email
HTML→text), `markdown-it-py` (Markdown→blocks), `python-calamine`
(spreadsheets), `puremagic` (mime sniffing), `structlog`, `mcp` + `uvicorn`
(serve). Standard library covers `sqlite3` (chat db, caches), `subprocess`
(notmuch JSON), hashing, and TOML.

Deliberately **absent** from the orchestrator: any ML framework. All heavy
extraction lives behind the Docker/HTTP boundary; all embedding compute lives in
the bare-metal model — Fundus only orchestrates the HTTP calls (fan-out) and caches
the resulting vectors. The Python process stays light.

**Services (Docker):** Meilisearch and the extraction engines (docling-serve,
Apache Tika). **Bare metal:** the embedding model on an OpenAI-compatible endpoint.

## Configuration

A single TOML file (`$XDG_CONFIG_HOME/fundus.toml` or `~/.config/fundus.toml`),
env-overridable, with a value-with-env-fallback convention for secrets. See
[`config/fundus.example.toml`](../config/fundus.example.toml). Every source and
path is supplied here — the toolkit ships with no built-in locations.

**Secrets.** Keys (`FUNDUS_MEILI_KEY`, `FUNDUS_SERVE_TOKEN`, `FUNDUS_EMBED_KEY`)
come from the environment. A configurable `env_file` (e.g. `~/.config/fundus.env`,
overridable with `$FUNDUS_ENV_FILE`) is **sourced at startup by every `fundus` /
`fundus-client` invocation** — via bash, so quoting and `export` work — so the
keys can live in one file rather than the shell, and so launchd jobs (which exec
`fundus` directly) get them without any wrapper. The default is unset; no path is
baked into the toolkit.

**Data layout.** All runtime data lives under one root — `[storage].data_dir`,
defaulting to `$XDG_DATA_HOME/fundus` — in named subdirectories: `meili/` (the
search index, mounted into the container), `cache/` (extraction + vector SQLite
caches), and `state/` (per-source cursors and the run lock). `fundus paths` prints
the resolved locations; `make up` reads `fundus paths --meili-data` to mount the
index, so the config is the single source of truth for where data lives. Only the
config file itself follows `XDG_CONFIG_HOME` independently.

## Deployment & security

- Run Meilisearch and the extraction engines as containers bound to localhost;
  run the embedding model bare-metal.
- The **indexer uses an admin key**; **consumers use a read-only search key**.
  Consumers can run as a separate, least-privileged OS user with no write path to
  the index and no filesystem access to the corpus.
- When isolating the embedding model from other model servers, you can run it
  under a dedicated account while **sharing the model weights on disk** to avoid
  duplicating them across installations.
