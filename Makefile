# Build/dev tasks — run from the HOST.
#
# The Fundus orchestrator is a host-side Python app; only the services
# (Meilisearch + extraction engines) run in containers, reached over HTTP and
# managed here via `docker compose`. There is no dev container: lint/test/format
# run against the venv on the host; up/down manage the service containers.

VENV    ?= .venv
PY      := $(VENV)/bin/python
COMPOSE ?= docker compose -f docker/compose.yml
# Tooling is overridable so environments without the dev venv (e.g. CI, which installs
# straight into its interpreter) can run the same targets: `make lint test RUFF=ruff ...`.
RUFF    ?= $(VENV)/bin/ruff
MYPY    ?= $(VENV)/bin/mypy
PYTEST  ?= $(VENV)/bin/pytest

# One shell command (backslash-continued), so `define` expands to a single recipe line.
define require-pipx
	@command -v pipx >/dev/null 2>&1 || { \
		echo "pipx not found — install it first:"; \
		echo "  brew install pipx && pipx ensurepath     # macOS (Homebrew Python is externally managed)"; \
		echo "  python3 -m pip install --user pipx       # other Python"; \
		exit 1; }
endef

# Prefer the dev venv's CLI; fall back to a `fundus` installed on PATH (via `make install`).
FUNDUS := $(shell [ -x $(VENV)/bin/fundus ] && echo $(VENV)/bin/fundus || echo fundus)
# Where Meilisearch persists its index: resolved from the user's config (single data root),
# falling back to the compose default if Fundus isn't set up yet.
MEILI_DATA := $(shell $(FUNDUS) paths --meili-data 2>/dev/null || echo ./data/meili)

.PHONY: build install dev fmt lint test clean up down

.DEFAULT_GOAL := build

build:  ## Build the wheel + sdist into ./dist (no install).
	$(require-pipx)
	pipx run build

clean:  ## Remove build artifacts.
	rm -rf dist

install:  ## Install the fundus + fundus-client CLIs for use (isolated, survives deleting this repo).
	$(require-pipx)
	pipx install --force .   # exposes both console scripts (fundus, fundus-client); --force = upgrade in place

dev:  ## Set up the editable dev environment in ./$(VENV).
	python3 -m venv $(VENV)
	$(PY) -m pip install --upgrade pip
	$(PY) -m pip install -e ".[dev]"

fmt:
	$(RUFF) format src tests

lint:
	$(RUFF) check src tests
	$(MYPY) src

test:
	$(PYTEST)

up:
	FUNDUS_MEILI_DATA="$(MEILI_DATA)" $(COMPOSE) up -d

down:
	FUNDUS_MEILI_DATA="$(MEILI_DATA)" $(COMPOSE) down
