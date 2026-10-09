# Chapter 03: Silver layer (clean)

**What we build:** `make silver` turns bronze into typed, deduplicated tables in `silver.*`.
A full rebuild of about 950,000 bronze rows takes about 15 seconds. Every run after that
only reads bronze batches it has not seen.

**What silver fixes (each one is a defect from chapter 1)**

| Defect | Silver rule |
|--------|-------------|
| Duplicate orders | `row_number() OVER (PARTITION BY order_id ORDER BY _ingested_at DESC)`, keep 1 |
| Status spellings | lookup in `silver.status_map`; unknown spellings are rejected, never guessed |
| UTC vs IST | keep `order_ts_utc`, add `order_ts_ist` and `order_date_ist`; the payment gateway's naive time is declared IST with `AT TIME ZONE 'Asia/Kolkata'` |
| Missing city / cuisine | default `'Unknown'` plus a flag column (`city_was_null`, `cuisine_was_blank`) |
| Impossible delivery minutes | outside 1 to 240: order goes to `silver.rejected_rows` with the raw row |
| Orphan order items | `reason = 'orphan'`; items of rejected orders get `parent_order_rejected` |

Real run: 152,247 bronze order rows become 149,550 silver orders (150,000 unique orders
minus 450 rejected outliers). 480,614 item rows become 478,181 (959 orphans, 1,474 items
of rejected orders).

**How incremental works:** `ops.pipeline_state` stores the last `_batch_id` silver has
processed per table. Each run reads batches after that mark, then moves it forward. The
run is one transaction, so a failure leaves silver and the marks untouched.
`make silver-full` resets the marks and rebuilds everything.

**Late parents:** a refund can arrive before the late order it belongs to. It is held in
`silver.rejected_rows` as `orphan` and re-checked every run. When the order shows up,
the refund is promoted and the reject row is removed. A test proves this.

**Commands:** `make silver`, `make silver-full`, `make test-03`

**Common mistakes**
- Treating the gateway's naive timestamps as UTC. Every payment then looks 5h30m before
  its order. The test checks every payment lands 0 to 91 seconds after its order.
- Grouping "orders per day" by the UTC date. Use `order_date_ist`. Orders after 18:30 UTC
  belong to the next day in India.
- Dropping bad rows. Silver keeps them with a reason, so you can answer "where did those
  450 orders go?".
- A bare `::timestamptz` cast. One bad value crashes the whole load. The `try_*` functions
  return NULL and the row is rejected with a reason instead.

**Design choices**
- Rejecting an order for an impossible delivery time also removes its revenue. That is a
  business call; the alternative is keeping the order and nulling the minutes. We follow
  the spec, and the rule is one `CASE` line in `sql/silver/30_orders.sql`.
- Payments and ratings are not checked against orders. Gold does not need that link, and
  chapter 5 adds a relationship check.
- Silver still holds PII (name, phone, email). Gold moves it into a restricted table.

**Interview line:** "Silver is idempotent and incremental: it keeps a batch high-water
mark per source, dedupes with a window function, and sends every rejected row to a
quarantine table with a reason instead of dropping it."
