# Deployment & machine-specific notes

Fundus is environment-agnostic: this repository contains no paths, hostnames, or
data specific to any one machine. Your deployment specifics — where your corpus
lives, which embedding endpoint you run, which hosts your services bind to —
belong **outside** version control.

## Where to keep them

Put your machine-specific deployment notes in:

```
~/.config/fundus/DEPLOYMENT.local.md
```

This path is intentionally outside the repository, and `*.local.md` is
git-ignored so the file stays out even if you keep it inside a checkout. Keep your
runtime configuration next to it (default `~/.config/fundus.toml`; see
[`config/fundus.example.toml`](../config/fundus.example.toml)).

## Why — and the code-assistant angle

If you use an AI coding assistant or agent, **point its context at
`~/.config/fundus/DEPLOYMENT.local.md`** (for example, reference that path from
your `CLAUDE.md`, `AGENTS.md`, or equivalent). The assistant then knows your real
paths, ports, and hosts without any of them leaking into the shared repository —
keeping the project portable for everyone else.

## Template

Copy this into `~/.config/fundus/DEPLOYMENT.local.md` and fill in your specifics:

```markdown
# Fundus — local deployment

## Sources
- mail (notmuch):    db    = /path/to/maildir
- chat (wacli):      db    = /path/to/whatsapp.db
- documents (files): roots = [/path/to/docs, ...]

## Services
- Meilisearch:     http://127.0.0.1:7700   (admin key via FUNDUS_MEILI_KEY)
- docling-serve:   http://127.0.0.1:5001
- tika:            http://127.0.0.1:9998
- Embedding model: http://127.0.0.1:8081/v1/embeddings  (bare-metal; model = ...)

## Consumers
- Read-only search key for: <who/what queries the index>

## Operations
- Schedule: <how/when incremental + full reconcile run>
- Notes:    <container runtime, mounts, GPU, scheduling>
```

## Apple Silicon: OCR via Apple Vision

On macOS the extraction container cannot reach Apple's Vision framework or the
GPU, so its OCR falls back to a CPU engine (EasyOCR) — noticeably slower and
weaker on mixed German/English scans. To use **Apple Vision (ocrmac)** instead,
run docling-serve **bare-metal** on the host (as with the embedding model) and
let fundus talk to it over HTTP:

1. Install docling-serve into a host virtualenv with the `ocrmac` extra available
   (Apple Vision has no model downloads; it uses the system framework).
2. Run it on the host, e.g. `docling-serve run --host 127.0.0.1 --port 5001`, and
   **stop the containerized docling-serve** so the port is free (leave Meilisearch
   and tika in Docker).
3. In `fundus.toml`, point the engine at the host server and select the engine:
   ```toml
   [extractor.engines.docling-serve]
   url = "http://127.0.0.1:5001"
   ocr_engine = "ocrmac"
   ```
4. Optionally let `fundus service` keep the server alive alongside the index/serve
   jobs by setting `[service.docling]` (`enabled`, `command`, `environment`); then
   `fundus service install` installs a `<prefix>.docling` keep-alive job too.
   To hold docling-serve's memory only while indexing, skip the keep-alive job
   and give the engine the same command line as an on-demand `start`
   (`[extractor.engines.docling-serve].start`): each run raises it on first use
   and stops it at the end.

`ocr_engine` is unset by default; containerized/Linux deployments need not set it.

For one-off OCR of individual PDFs outside the indexing pipeline (including
per-file control of the rasterization scale that docling-serve's HTTP API does
not expose), [pdfsnz](https://github.com/smartpointer-com/pdfsnz) drives the
same docling + ocrmac stack as a standalone CLI.
