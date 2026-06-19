.PHONY: install fmt lint test up down

install:
	uv sync || pip install -e ".[dev]"

fmt:
	ruff format src tests

lint:
	ruff check src tests
	mypy src

test:
	pytest

up:
	docker compose -f docker/compose.yml up -d

down:
	docker compose -f docker/compose.yml down
