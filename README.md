# Swiggy AI Data Assistant

Ask business questions in plain English. A multi-agent system answers from a real
data warehouse, shows the SQL, fixes its own failed queries, catches silently wrong
results, and never exposes PII. All data is generated sample data.

> Status: **Phases 0-9 done** (setup, synthetic data, bronze, silver, gold, data quality, semantic layer, security, the multi-agent assistant, audit and reporting). The README grows with each chapter.

## Prerequisites
- Docker Desktop (running)
- Python 3.11+
- An OpenAI API key (set a usage limit in the OpenAI dashboard first)

## Quick start
```bash
make venv          # 1. create .venv, install requirements
make env           # 2. create .env from .env.example
# 3. edit .env: set OPENAI_API_KEY and OPENAI_MODEL
make up            # 4. start Postgres 16 (waits until healthy)
make check         # 5. verify env, database, roles and one tiny LLM call
make data          # 6. generate the sample data into data/raw (about 3 seconds)
make bronze        # 7. load the raw files into bronze tables (run it twice: second run loads nothing)
make silver-full   # 8. clean bronze into silver (about 15 seconds); later `make silver` is incremental
make gold-full     # 9. build the star schema (about 13 seconds); later `make gold` is incremental
make dq            # 10. run the data quality checks (45 of them)
make dq-demo       # 11. break a copy of gold on purpose and watch the checks catch it
make catalog-check # 12. confirm semantic/catalog.yml describes every analytics view column
make security      # 13. roles, grants, row-level security, session limits (then verifies them)
make seed-users    # 14. demo users: admin, Bengaluru manager, Mumbai manager (keys from .env)
make security-demo # 15. log in as the assistant and watch each attempt to break out fail
make report        # 16. how the assistant has been doing (from the audit trail)
make eval          # 17. score the assistant on 30 golden questions (calls OpenAI, costs a little)
```

## Make targets
Run `make` to list them. So far: `venv env up down reset-db psql check data bronze bronze-rerun-test bronze-reset silver silver-full gold gold-full views security security-verify seed-users security-demo dq dq-demo catalog-check lint test test-00 ... test-10 report eval eval-check eval-gate eval-compare ci`.

## Troubleshooting
- **`role "assistant_ro" does not exist`**: another Postgres is answering. This project
  uses host port **5433** (not 5432) so it does not clash with a local install.
  Check `POSTGRES_PORT` and the `DATABASE_URL_*` ports in `.env` match.
- **Cannot reach database**: start Docker Desktop, then `make up`.
- **Changed passwords in `.env`**: roles are created only on first start. Run `make reset-db`.

## About the data
Everything is generated (seed 42), nothing is real. `make data` writes source-system style
files to `data/raw/` and a ledger of the defects it injected to `data/raw/_defects.json`
(see `notes/chapter_01.md`). A few rows of each file type are in `data/sample/`.

The gold star schema is drawn in `docs/erd.md`.

## The semantic layer
`semantic/metrics.yml` (one SQL definition per metric), `semantic/catalog.yml` (every column the
assistant can see) and `semantic/glossary.md` are the single source of business meaning. Read
`notes/chapter_06.md`.

## Security in one paragraph
The assistant logs in as `assistant_ro`, which can read only the eight documented `analytics`
views, cannot write, runs under an 8-second timeout, and sees only the cities its user is
allowed (row-level security that fails closed). Set real values for `API_KEY`,
`API_KEY_BENGALURU` and `API_KEY_MUMBAI` in `.env` before `make seed-users`. Details and the
honest limits are in `notes/chapter_07.md`.

## The assistant
`src/swiggy_ai/agents/` is a LangGraph graph: router, clarifier, planner, schema agent, SQL writer,
guard, cost check, executor, healer, result checker, narrator and synthesizer. See
`notes/chapter_08.md` for the diagram and what each part guarantees. Set `OPENAI_API_KEY` and the
`OPENAI_MODEL*` names in `.env` (small, cheap models are enough); `make check` makes one tiny call to
confirm. From Python:

```python
from swiggy_ai.agents.service import Assistant
from swiggy_ai.security.scope import AppUser

answer = Assistant().ask("Why did refunds increase in Mumbai last month?",
                         AppUser("me", "Me", "ALL"))
print(answer.answer, answer.sql, answer.warnings)
```

## Evaluation
`make eval` asks the assistant the 30 questions in `eval/golden.yml` and scores each answer against
a hand-written reference query (`eval/reference_sql/`, written against the gold tables, not the views
the assistant reads). It writes `eval/results/<time>.json` and `.md` (not committed). Nothing in this
README states an accuracy figure on purpose: run `make eval` and read your own report. Related:
`make eval-check` (validates the golden file and every reference query; no model call),
`make eval-compare` (what changed since the last run), `make eval-gate` (pass/fail against the marks
you set in `eval/thresholds.yml`; it refuses to pass while that file is empty). Demo fault
injection is always off during an evaluation. See `notes/chapter_10.md`.

## Cost reporting
Add the price of each model in `.env` (`PRICE_INPUT_PER_1M`, `PRICE_OUTPUT_PER_1M`, US dollars per
million tokens, from the provider's pricing page; optional `_LOW`, `_MEDIUM`, `_HIGH` pairs when the
tiers use different models). Until you do, `make report` says `cost not computed (prices not set)`.
Every question is recorded in `ops.assistant_runs` with personal data scrubbed; see
`notes/chapter_09.md`.
