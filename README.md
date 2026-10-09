# Swiggy AI Data Assistant

Ask business questions in plain English. A multi-agent system answers from a real
data warehouse, shows the SQL, fixes its own failed queries, catches silently wrong
results, and never exposes PII. All data is generated sample data.

> Status: **Phase 0 (setup) done.** The README grows with each chapter.

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
```

## Make targets
Run `make` to list them. So far: `venv env up down reset-db psql check lint test test-00 ci`.

## Troubleshooting
- **`role "assistant_ro" does not exist`**: another Postgres is answering. This project
  uses host port **5433** (not 5432) so it does not clash with a local install.
  Check `POSTGRES_PORT` and the `DATABASE_URL_*` ports in `.env` match.
- **Cannot reach database**: start Docker Desktop, then `make up`.
- **Changed passwords in `.env`**: roles are created only on first start. Run `make reset-db`.
