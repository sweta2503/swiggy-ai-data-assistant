.DEFAULT_GOAL := help
.PHONY: help venv env up down reset-db psql check data bronze bronze-reset bronze-rerun-test silver silver-full gold gold-full views security security-verify seed-users security-demo dq dq-demo catalog-check lint test test-00 test-01 test-02 test-03 test-04 test-05 test-06 test-07 test-08 test-09 test-10 test-11 drift-demo refresh-catalog eval eval-check eval-gate eval-compare ci
PY := .venv/bin/python
export PYTHONPATH := src:.

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

silver:  ## Clean new bronze batches into silver (incremental)
	$(PY) -m swiggy_ai.pipeline.silver

silver-full:  ## Rebuild silver from all of bronze
	$(PY) -m swiggy_ai.pipeline.silver --full

gold:  ## Load new silver data into the gold star schema (incremental)
	$(PY) -m swiggy_ai.pipeline.gold

gold-full:  ## Empty gold and rebuild it from silver
	$(PY) -m swiggy_ai.pipeline.gold --full

views:  ## (Re)create the analytics views the assistant reads
	$(PY) -c "from swiggy_ai.db import connect; from swiggy_ai.pipeline.gold import apply_views; c = connect('etl', autocommit=True); apply_views(c); print('analytics views created')"

security:  ## Create roles, grants, row-level security and session limits, then verify them
	$(PY) -m swiggy_ai.security

security-verify:  ## Check the live database's security posture (exit 1 on a problem)
	$(PY) -m swiggy_ai.security --verify

seed-users:  ## Create the demo users (admin, Bengaluru, Mumbai) from the keys in .env
	$(PY) -m swiggy_ai.security --seed-users

security-demo:  ## Log in as the assistant and try to break out (needs data loaded)
	$(PY) -m swiggy_ai.security --demo

catalog-check:  ## Fail if a column in an analytics view is missing from semantic/catalog.yml
	$(PY) -m swiggy_ai.semantic

dq:  ## Run all data quality checks (exit 1 if a blocking check fails)
	$(PY) -m swiggy_ai.dq

dq-demo:  ## Corrupt a COPY of gold (duplicate a day) and show the checks catch it
	$(PY) -m swiggy_ai.dq --demo

drift-demo:  ## Write a changed-schema batch to data/drift and watch it get quarantined, then approved
	$(PY) -m swiggy_ai.pipeline.drift_demo

refresh-catalog:  ## Update catalog.yml from the database (columns, types) and list what needs a description
	$(PY) -m swiggy_ai.semantic --refresh

lint:  ## Ruff lint
	.venv/bin/ruff check .

test-00:  ## Phase 0 tests
	$(PY) -m pytest tests/phase00 -q

test-01:  ## Phase 1 tests
	$(PY) -m pytest tests/phase01 -q

test-02:  ## Phase 2 tests
	$(PY) -m pytest tests/phase02 -q

test-03:  ## Phase 3 tests
	$(PY) -m pytest tests/phase03 -q

test-04:  ## Phase 4 tests
	$(PY) -m pytest tests/phase04 -q

test-05:  ## Phase 5 tests
	$(PY) -m pytest tests/phase05 -q

test-06:  ## Phase 6 tests
	$(PY) -m pytest tests/phase06 -q

test-07:  ## Phase 7 tests
	$(PY) -m pytest tests/phase07 -q

test-08:  ## Phase 8 tests (offline ones; live ones run too if OPENAI_API_KEY is set)
	$(PY) -m pytest tests/phase08 -q

report:  ## How the assistant did over the last 7 days (from the audit trail)
	$(PY) -m swiggy_ai.ops.report

test-09:  ## Phase 9 tests
	$(PY) -m pytest tests/phase09 -q

eval:  ## Ask all 30 golden questions with the REAL model and score the answers (costs a little)
	$(PY) -m eval.run_eval

eval-check:  ## Validate the golden set and run every reference query; no model is called
	$(PY) -m eval.run_eval --dry-run

eval-gate:  ## Exit non-zero if the latest eval misses the pass marks in eval/thresholds.yml
	$(PY) -m eval.report gate

eval-compare:  ## What changed between the last two eval runs (regressions and fixes)
	$(PY) -m eval.report compare

test-10:  ## Phase 10 tests
	$(PY) -m pytest tests/phase10 -q

test-11:  ## Phase 11 tests
	$(PY) -m pytest tests/phase11 -q

test:  ## All tests
	$(PY) -m pytest -q

ci: lint test  ## Lint + tests (live tests skip without a key)
