# Chapter 12: Scheduling and caching

**One idea:** a pipeline that only runs when someone remembers is not a pipeline, and a model call
you have already paid for should not be paid for twice, until the data underneath changes.

**What was built**
- **`make daily`** runs bronze -> silver -> gold -> data quality, then, only if no blocking check
  failed, refreshes the catalog and publishes. Every run (including failed and blocked ones) is a row
  in `ops.pipeline_runs`: stages with timings, checks run, which blocking checks failed, open schema
  alerts, catalog changes, data version before and after.
  Exit codes: 0 published, 1 blocked by data quality, 2 a stage failed, 3 another run is in progress
  (a database lock stops two runs overlapping).
- **What "publish" means, honestly.** Gold is updated in place by the earlier stages, so a blocked run
  cannot un-load it. What the stop sign withholds is the new **data version**, the catalog refresh and
  the "published" status. The assistant's existing data-quality warning keeps telling readers the data
  is suspect, and answers cached from the last good data stay valid.
- **Data version.** `ops.data_version` is one counter. A published run that changed something (new
  files, batches or gold rows, or the first clean run after a blocked one) bumps it and deletes older
  cached answers.
- **`make scheduler`**: a small APScheduler process, 06:00 India time by default
  (`--cron "30 5 * * *"`, `--run-now`). No overlap, missed runs are merged, a crash is logged and the
  process carries on. Plain cron works too and needs no code (the line is in the file header).
- **Reference Airflow DAG** at `orchestration/airflow/swiggy_daily_dag.py`: same tasks and order,
  marked reference-only, Airflow is not a dependency. "Stop before publishing" is simply a failed
  `dq` task. Its last task runs `daily --publish-only`, which bumps the version only if the latest
  checks are healthy.
- **Answer cache** `ops.answer_cache`. The key hashes: the normalised question (case, spacing and
  trailing punctuation ignored), the data version, and the city scope. Hits are flagged in the answer
  (`cache_hit`, `cached_at`), logged, written to the audit trail (`cache_hit` column) and shown in
  `make report`, which leaves them out of the latency figures. `ask(..., fresh=True)` skips the lookup
  and replaces the stored answer; the API will expose that as `?fresh=true` in Phase 13.
- Live check with the real model: first ask 4,747 ms and 3,476 input tokens; the same question again
  1 ms and 0 tokens, identical answer and rows.

**Rules that keep the cache from lying**
- Only the first question of a conversation is looked up or stored ("and in Pune?" means nothing
  alone). A cache hit still writes the turn into the thread's memory, so a follow-up works.
- Only clean answers are stored: status answered, route simple or multi-step, no warnings (other than
  "repaired automatically"), no injected fault. Refusals, clarifying questions and failures are not.
- A question that looks personal (phone, email, card, id) is never stored.
- A different city scope or a newer data version can never be served an older answer.
- A broken cache never breaks answering: errors are logged and the question is answered normally.
- Off by default in code (`Assistant(cache=True)` turns it on), so tests and `make eval` always
  measure the real thing. `ANSWER_CACHE=true` in `.env` is for the interfaces in Phase 13.

**Not done yet**
- A long-running assistant (API) keeps the catalog it loaded at start; after `make daily` rewrites
  `catalog.yml` it needs a reload. Phase 13 should reload when the data version changes.
- The cache ignores prompt and model changes. After editing a prompt, run `make cache-clear`.

**Your steps**
1. `make daily` (a no-op today: nothing new, version stays 1). Look at `ops.pipeline_runs`.
2. Choose how to schedule it: `make scheduler` in a terminal, or the cron line in
   `src/swiggy_ai/pipeline/scheduler.py`.
3. To see the cache work, ask the same question twice with `Assistant(cache=True)`; `make report` shows
   the hit.

**Commands:** `make daily`, `make scheduler`, `make cache-clear`, `make test-12`

**Common mistakes**
- Caching without the data version in the key, so yesterday's number is served after a refresh.
- Caching follow-up questions by their words alone.
- Publishing after a failed quality check "because the load finished".
- Letting the cache share answers between city scopes.
- Counting cache hits in the latency numbers, which makes the system look faster than it is.

**Interview line:** "The daily run stops before publishing if a blocking check fails and records every run;
cached answers are keyed by question, data version and city scope, so a refresh retires them all and one
scope can never see another's."
