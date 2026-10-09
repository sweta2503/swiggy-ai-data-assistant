# Chapter 00: Setup

**What we build:** Postgres 16 in Docker with six schemas (bronze, silver, gold,
analytics, semantic, ops), two login roles (`etl_writer`, `assistant_ro`), typed
settings, one module for OpenAI, and `make check`.

**Why it matters at work:** a one-command, repeatable environment means anyone can
reproduce a bug. Separate DB roles from day one means security is not retrofitted.

**Commands:** `make venv env up check`

**Common mistakes**
- Another Postgres already on port 5432 silently answers instead of Docker (we use 5433).
- Editing passwords in `.env` after the first start: roles already exist, so `make reset-db`.
- Hard-coding a model name: it lives in `OPENAI_MODEL` so you can switch without code changes.

**Interview line:** "I isolate every external dependency behind one module and one role,
so swapping the LLM or tightening permissions is a one-file change."
