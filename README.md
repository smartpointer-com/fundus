# Fundus

Fundus is a self-hostable toolkit that indexes a heterogeneous corpus — **email,
chat messages, and document files** — into [Meilisearch](https://www.meilisearch.com/),
giving you full-text **and** hybrid (keyword + semantic) search designed for
consumption by agents.

It is built around two plugin families, both swappable by configuration:

- **Sources** — where data comes from (a notmuch mail store, a chat database, a
  file tree). Nothing is hard-coded to a particular filesystem layout.
- **Extraction engines** — how document bytes become text. An engine-agnostic
  interface lets you swap, A/B-test, or *escalate* between engines (docling-serve,
  Apache Tika) without touching the rest of the pipeline.

## How it fits together

```
sources ──▶ extraction (engine-agnostic) ──▶ chunking ──▶ Meilisearch ──▶ agents
 mail        docling-serve / tika / ...        text /                       (read-only
 chat         (Docker, swappable)              chat /        ▲               search)
 files                                         tabular       │
                                                     embeddings via a bare-metal,
                                                     OpenAI-compatible model endpoint
```

- The **search engine and extraction engines run in Docker**; the **embedding
  model runs bare-metal** and is reached over HTTP.
- The indexer writes with an admin key; **consumers search with a read-only key**.

See [docs/architecture.md](docs/architecture.md) for the full design,
[docs/decisions.md](docs/decisions.md) for the rationale behind the major
choices, and [docs/deployment.md](docs/deployment.md) for where to keep
machine-specific setup notes.

## Quick start

```bash
make install                       # install the fundus + fundus-client CLIs (isolated; survive deleting this repo)
cp config/fundus.example.toml ~/.config/fundus.toml   # then edit paths (and optionally [storage].data_dir)
make up                            # start Meilisearch + extraction engines (mounts your data root)
fundus init                        # apply index settings
fundus index                       # ingest (incremental)
fundus serve                       # expose read-only search over MCP
```

All runtime data (the search index, caches, cursors) lives under one root — by
default `$XDG_DATA_HOME/fundus`, overridable via `[storage].data_dir` in the
config. Run `fundus paths` to see the resolved locations. `make up`/`make down`
manage the service stack from this repo.

`fundus index --full` reconciles the index against reality (deletions, edits the
mtime cursor can't see, and documents whose extraction settings changed since
they were parsed). `fundus reparse --ocr-only` (or `--path-prefix`/`--source`)
force-re-extracts selected documents past the cache — for when an extraction
engine *upgrade* seems worth a re-OCR; a mere config change needs no manual
step, the next `--full` converges it.

## Service jobs (macOS)

`fundus service install` sets up up to four launchd jobs pointing at the installed CLI —
incremental indexing (every 30 min), a nightly full reconcile, and the read-only
MCP server (kept alive), plus an optional bare-metal docling-serve (opt-in, off by
default; see below):

```bash
make install                       # the service must point at an installed binary, not the dev venv
fundus service install             # LaunchAgent (login session); --daemon for a headless LaunchDaemon
fundus service install --no-serve  # index jobs only;  --no-index for a server-only host
fundus service status              # state, last exit, run count (all jobs)
fundus service run                 # trigger an incremental run now (test without waiting)
fundus service restart --serve     # restart the MCP server (e.g. after a config change)
fundus service uninstall
```

Flags `--interval`, `--full-at HH:MM`, and `--label-prefix` (or the `[service]`
config block) tune it; the server's host/port/transport come from `[serve]`. The
index/serve jobs run `fundus` directly and get their secrets from the configured
`env_file` (see below). Set `--full-at` *after* whatever syncs your corpus to disk.
Logs land in `<data_root>/logs/`.

On Apple Silicon, `--docling` (or `[service.docling]`) adds a fourth job that keeps a
**bare-metal docling-serve** alive — needed for Apple Vision (ocrmac) OCR, which the
containerized engine can't reach. See
[docs/deployment.md](docs/deployment.md#apple-silicon-ocr-via-apple-vision).

## Read-only access for agents (MCP)

`fundus serve` runs a read-only MCP server exposing `search`, `sources`, and
`locate` tools — the surface you hand to an agent. It binds only to a search-scoped
Meili key (never the admin key) and, over HTTP, is gated by a bearer token:

```bash
export FUNDUS_SERVE_TOKEN=…         # the token you share with the agent (required over HTTP)
# export FUNDUS_MEILI_SEARCH_KEY=…  # optional: a scoped key; else it uses the admin key it holds
fundus serve                        # streamable-HTTP on 127.0.0.1:8181 by default
```

(Or put those in a secrets file and set `env_file` in the config — every `fundus` /
`fundus-client` invocation sources it at startup, so you don't export by hand.)

To keep it running, install it as a managed daemon instead of launching by hand —
`fundus service install` includes it as the `<prefix>.serve` job (see *Service jobs*).
The server reads the config once at startup: restart it after adding or removing
sources, or its `sources`/`locate` tools keep answering from the old config.

To point a client at it, `fundus connect` prints ready-to-paste registrations
(OpenClaw, Claude Code, or a generic JSON stanza) with the endpoint and token
filled in from config:

```bash
fundus connect openclaw   # → openclaw mcp add fundus --url http://127.0.0.1:8181/mcp --header "Authorization: Bearer …"
```

[skills/fundus-search/SKILL.md](skills/fundus-search/SKILL.md) is a ready-made
agent skill describing the three tools; copy it into your agent's skills
directory. The full walkthrough — persistence on macOS/Linux, registration,
skill install, verification — is in [docs/agents.md](docs/agents.md).

For humans and shell scripts, **`fundus-client`** is a thin MCP client (holds only
the endpoint + token, no Meili keys):

```bash
fundus-client query "electricity bill" --json
fundus-client sources
fundus-client locate "<a ref from a search hit>"
```

## Local deployment notes

Keep your machine-specific wiring (corpus paths, service hosts/ports, the
embedding endpoint) in `~/.config/fundus/DEPLOYMENT.local.md` — outside this repo,
which stays generic. If you use an AI coding assistant, point its context at that
file so it knows your environment without leaking it into the repository. See
[docs/deployment.md](docs/deployment.md).

## Status

The pipeline is implemented end-to-end, unit-tested (`ruff` + `mypy --strict`
clean), and validated against live services on a real corpus. Beyond the core it
adds an escalating extraction router (tika-first, docling for scans, forced OCR
as a last rung), fan-out embedding with a reusable vector cache, and indexing of
email/chat document attachments.

Roadmap: a client-side reranker after hybrid retrieval; media transcription
(voice notes, image captioning); semantic chat segmentation over the window
heuristics; a real tokenizer for chunk budgets (chars/4 heuristic today);
integration tests against live services. Note before publishing to a package
index: the bare name `fundus` is taken by an unrelated package on at least one
index.

## Development

```bash
make dev       # create the editable dev environment in ./.venv
make lint      # ruff + mypy
make test      # pytest
make up        # start Meilisearch + extraction engines (Docker)
make           # build the wheel + sdist into ./dist (installs nothing)
```

(`make install` is the *user* install — it puts the `fundus` and `fundus-client`
CLIs on your PATH via `pipx`, independent of this tree. Contributors want `make dev`.)

## License

[MIT](LICENSE) — Copyright (c) 2026 SmartPointer AG.
