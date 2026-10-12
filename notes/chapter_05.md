# Chapter 05: Data quality

**What we build:** 45 checks over silver and gold, a results table (`ops.dq_results`), and
`assert_gold_healthy()` so the assistant can warn readers when the data is not safe to trust.
`make dq` runs all of them; `make dq-demo` breaks a copy of gold on purpose.

**Why it matters at work:** a pipeline that runs is not a pipeline that is right. Silent problems
(a day loaded twice, a stalled feed, numbers drifting between layers) cost more than loud ones, and
the constraints in the database do not catch them: a primary key stops a duplicate in one month,
not the same order landing in two.

**A check is one measurement against one threshold:** `observed <= threshold` passes.

| Kind | Catches |
|---|---|
| `not_null`, `unique`, `accepted_values`, `relationships` | missing keys, duplicates, unmapped spellings, orphans |
| `freshness`, `row_count_delta` | a stalled pipeline, a half or double load |
| `range`, `reconciliation`, `scd2_integrity` | impossible values, layers disagreeing, broken history |

**Blocking vs warning.** A blocking failure stops a publish and makes `assert_gold_healthy()`
return False; a warning (a new refund reason code, say) is reported only. One broken check never
stops the rest; it is stored as `error`. Health reads the *latest* run instead of re-running every
check on each question, and demo runs never count.

**The demo.** `make dq-demo` copies gold to a scratch schema, loads the newest day a second time
there, and runs the gold checks on the copy: the `unique` check reports exactly that day's order
count as duplicates. Real gold is never touched (it uses the admin role, which can create schemas).

**Commands:** `make dq`, `make dq-demo`, `make test-05`

**Common mistakes**
- Checks that have never failed: every check here has a test that breaks the data and sees it fail.
- Measuring freshness against the real clock on demo data that stops on a fixed day.
- Alerting on every wobble: `row_count_delta` is a warning because festivals legitimately double a day.
- Counting an expected exception as a failure: payments of orders silver rejected are excused, with
  the reason written into the check.

**Interview line:** "I check each layer for completeness, uniqueness, validity, freshness and
reconciliation to the layer below, store every result, and gate publishing and answers on the
blocking ones."
