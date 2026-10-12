# Chapter 12: Scheduling and caching

**What we build:** `make daily`, a scheduler, a reference Airflow DAG, a data version, and an answer
cache.

**Why it matters at work:** a pipeline that only runs when someone remembers is not a pipeline, and a
model call you already paid for should not be paid for twice until the data underneath changes.

**`make daily`** runs bronze, silver, gold and data quality, then, only if no blocking check failed,
refreshes the catalog and publishes. Each run is a row in `ops.pipeline_runs` (stages with timings,
checks, which blocking checks failed, open schema alerts, data version before and after), including
blocked and failed ones. Exit codes: 0 published, 1 blocked, 2 a stage failed, 3 another run is in
progress (a database lock). Open schema alerts are reported but do not stop a healthy run.

**What "publish" means, honestly:** gold is updated in place by the earlier stages, so a blocked run
cannot undo it. The stop sign withholds the new **data version**, the catalog refresh and the
"published" status; the data-quality warning keeps showing, and answers cached from the last good data
stay valid.

**Scheduling.** `make scheduler` is a small APScheduler process (06:00 India time; `--cron`,
`--run-now`; no overlap; a crash is logged, not fatal). Plain cron needs no code. The Airflow DAG in
`orchestration/airflow/` has the same tasks and is marked reference-only; "stop before publishing" is
just a failed `dq` task, and its last task runs `daily --publish-only`.

**The answer cache** (`ops.answer_cache`). Key = normalised question + data version + hash of the city
scope. A published run that changed something bumps the version and deletes older answers. Hits are
flagged (`cache_hit`), logged, audited, and shown in `make report` (excluded from latency).
`ask(..., fresh=True)` skips the lookup.

**Rules that keep it honest:** only the first question of a conversation is cached (a hit still enters
the thread's memory so follow-ups work); only clean answers are stored (no warnings, no injected
fault, nothing that looks personal); a broken cache never breaks answering. It is off by default in code
(`Assistant(cache=True)`), so tests and `make eval` measure the real thing.

**Limits:** the cache ignores prompt or model changes (run `make cache-clear` after editing a prompt).

**Commands:** `make daily`, `make scheduler`, `make cache-clear`, `make test-12`

**Common mistakes**
- Caching without the data version in the key, so yesterday's number is served after a refresh.
- Caching follow-up questions by their words alone.
- Publishing after a failed quality check "because the load finished".
- Sharing cached answers between city scopes.
- Counting cache hits in latency, which flatters the system.

**Interview line:** "The daily run stops before publishing if a blocking check fails and records every
run; cached answers are keyed by question, data version and city scope, so a refresh retires them all
and one scope can never see another's."
