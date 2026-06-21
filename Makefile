# Build/dev tasks — run from the HOST.
#
# The Fundus orchestrator is a host-side Python venv; only the services
# (Meilisearch + extraction engines) run in containers, reached over HTTP and
# managed here via `docker compose`. There is no dev container: lint/test/format
# run against the venv on the host; up/down manage the service containers.

VENV    ?= .venv
PY      := $(VENV)/bin/python
COMPOSE ?= docker compose -f docker/compose.yml

.PHONY: install fmt lint test up down

install:
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
	$(COMPOSE) up -d

down:
	$(COMPOSE) down
