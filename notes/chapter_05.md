# Chapter 05: Data quality

**What we build:** 45 checks over silver and gold, a results table (`ops.dq_results`), and
`assert_gold_healthy()` so the assistant can warn users when the data is not safe to trust.
`make dq` runs everything in about a second. `make dq-demo` breaks a copy of gold on purpose.

**A check is one measurement against one threshold.**

    observed  = what we measured (bad rows, hours late, a ratio)
    threshold = the most we accept
    pass      = observed <= threshold

| Kind | Example | What it catches |
|---|---|---|
| `not_null` | `gold.fact_orders.customer_sk` | missing keys |
| `unique` | `gold.fact_orders.order_id` | a day loaded twice |
| `accepted_values` | `status` in 4 values | a new, unmapped spelling |
| `relationships` | items need an order | orphans, broken joins |
| `freshness` | newest order within 36h | a stalled pipeline |
| `row_count_delta` | today vs yesterday, max 1.8x | half or double loads |
| `range` | `delivery_minutes` 1 to 240 | impossible values |
| `reconciliation` | gold revenue = silver revenue, per day | numbers drifting between layers |
| `scd2_integrity` | no overlaps, no gaps, one current row | broken history |

**Blocking vs warning.** A blocking failure stops a publish and makes
`assert_gold_healthy()` return False. A warning (new refund reason code, payments without an
order) is reported but does not block. One broken check never stops the rest; it is stored as
`error`.

**Reading health.** `assert_gold_healthy(conn)` reads the latest run instead of re-running
every check on each question. It is False if a blocking check failed, if there is no run yet,
or (optionally) if the run is too old. Its message is ready to show:
`data quality warning: freshness check failed`. Demo runs never count.

**The demo.** `make dq-demo` copies gold to a scratch schema, loads the newest day a second
time there, and runs the gold checks on the copy. Real run: 5 checks fail, 4 blocking, and
`unique` reports exactly 481 duplicate orders, which is that day's order count. The scratch
schema is dropped afterwards and real gold is never touched. It uses the admin role because
the pipeline role (rightly) cannot create schemas.

**Why the checks matter even though the database has constraints.** The primary key stops
the same order being loaded twice into the same month. It does not stop it landing in two
different months, a wrong sum, a missing day or a stale pipeline. Constraints guard rows;
checks guard the whole dataset.

**Commands:** `make dq`, `make dq-demo`, `make test-05`

**Common mistakes**
- Checks that have never failed. Every check here has a test that breaks the data and sees it
  fail.
- Testing "freshness" against the real clock on demo data. The data stops on a fixed day, so
  the demo treats the end of that day as "now".
- Alerting on every wobble. `row_count_delta` is only a warning because festivals double a
  day legitimately.
- Counting an expected exception as a failure. Payments of orders that silver rejected are
  excused, with the reason written into the check.

**Interview line:** "I check each layer for completeness, uniqueness, validity, freshness and
reconciliation to the layer below, store every result, and gate publishing and answers on the
blocking ones."
