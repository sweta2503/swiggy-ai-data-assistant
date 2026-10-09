.DEFAULT_GOAL := help
.PHONY: help venv env up down reset-db psql check data bronze bronze-reset bronze-rerun-test lint test test-00 test-01 test-02 ci
PY := .venv/bin/python
export PYTHONPATH := src

help:  ## Show this help
	@grep -E '^[a-zA-Z0-9_-]+:.*##' $(MAKEFILE_LIST) | awk -F':.*## ' '{printf "  %-16s %s\n", $$1, $$2}'

venv:  ## Create .venv and install requirements (idempotent)
	@test -d .venv || python3 -m venv .venv
	@.venv/bin/pip install -q -r requirements.txt

env:  ## Create .env from .env.example if missing
	@test -f .env || (cp .env.example .env && echo "Created .env - now add OPENAI_API_KEY and OPENAI_MODEL")

up: env  ## Start Postgres and wait until healthy
	docker compose up -d --wait

down:  ## Stop Postgres (data is kept)
	docker compose down

reset-db:  ## DANGER: delete the database volume and start fresh
	docker compose down -v
	docker compose up -d --wait

psql:  ## Open a psql shell as admin
	docker compose exec postgres psql -U $${POSTGRES_ADMIN_USER:-swiggy_admin} -d swiggy

check:  ## Verify env, database, roles and one tiny LLM call
	$(PY) -m swiggy_ai.check

data:  ## Generate deterministic sample data into data/raw (seed 42)
	$(PY) -m swiggy_ai.datagen --seed 42

bronze:  ## Load new source files into bronze (skips files already loaded)
	$(PY) -m swiggy_ai.pipeline.bronze_loader

bronze-reset:  ## Forget all loaded bronze data, then reload from data/raw
	$(PY) -m swiggy_ai.pipeline.bronze_loader --reset

bronze-rerun-test:  ## Load twice; fail unless the second run loads nothing
	$(PY) -m swiggy_ai.pipeline.bronze_loader --rerun-test

lint:  ## Ruff lint
	.venv/bin/ruff check .

test-00:  ## Phase 0 tests
	$(PY) -m pytest tests/phase00 -q

test-01:  ## Phase 1 tests
	$(PY) -m pytest tests/phase01 -q

test-02:  ## Phase 2 tests
	$(PY) -m pytest tests/phase02 -q

test:  ## All tests
	$(PY) -m pytest -q

ci: lint test  ## Lint + tests (live tests skip without a key)
