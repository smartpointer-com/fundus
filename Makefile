# Build/dev tasks — run from the HOST.
#
# The Fundus orchestrator is a host-side Python app; only the services
# (Meilisearch + extraction engines) run in containers, reached over HTTP and
# managed here via `docker compose`. There is no dev container: lint/test/format
# run against the venv on the host; up/down manage the service containers.

VENV    ?= .venv
PY      := $(VENV)/bin/python
COMPOSE ?= docker compose -f docker/compose.yml

# Prefer the dev venv's CLI; fall back to a `fundus` installed on PATH (via `make install`).
FUNDUS := $(shell [ -x $(VENV)/bin/fundus ] && echo $(VENV)/bin/fundus || echo fundus)
# Where Meilisearch persists its index: resolved from the user's config (single data root),
# falling back to the compose default if Fundus isn't set up yet.
MEILI_DATA := $(shell $(FUNDUS) paths --meili-data 2>/dev/null || echo ./data/meili)

.PHONY: install dev fmt lint test up down

install:  ## Install the fundus + fundus-client CLIs for use (isolated, survives deleting this repo).
	@command -v pipx >/dev/null 2>&1 || { \
		echo "pipx not found — install it first:"; \
		echo "  brew install pipx && pipx ensurepath     # macOS (Homebrew Python is externally managed)"; \
		echo "  python3 -m pip install --user pipx       # other Python"; \
		exit 1; }
	pipx install --force .   # exposes both console scripts (fundus, fundus-client); --force = upgrade in place

dev:  ## Set up the editable dev environment in ./$(VENV).
	python3 -m venv $(VENV)
	$(PY) -m pip install --upgrade pip
	$(PY) -m pip install -e ".[dev]"

fmt:
	$(VENV)/bin/ruff format src tests

lint:
	$(VENV)/bin/ruff check src tests
	$(VENV)/bin/mypy src

test:
	$(VENV)/bin/pytest

up:
	FUNDUS_MEILI_DATA="$(MEILI_DATA)" $(COMPOSE) up -d

down:
	FUNDUS_MEILI_DATA="$(MEILI_DATA)" $(COMPOSE) down
