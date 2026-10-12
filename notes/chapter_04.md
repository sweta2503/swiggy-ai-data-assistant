# Chapter 04: Gold layer (star schema)

**What we build:** `make gold-full` turns silver into a star schema: three facts (`fact_orders`,
`fact_order_items`, `fact_refunds`), five dimensions, a restricted `customer_pii` table and the
`daily_city_sales` aggregate. Diagram and grains: `docs/erd.md`.

**Why it matters at work:** analysts and BI tools want one simple shape. Facts hold numbers,
dimensions hold labels, and "revenue by city by month" is one join away.

**SCD Type 2.** A customer who moves from Delhi NCR to Pune gets two rows, each with
`valid_from` and `valid_to`. Windows are half-open (`valid_from <= date < valid_to`), so there is
never a gap or an overlap, and a partial unique index allows one current row per customer. Each
order points at the version valid *on its date*, so last year's Delhi orders stay Delhi orders.
Surrogate keys never change: a re-run closes the old row, then opens the new one.

**Partitioning.** `fact_orders` is range-partitioned by month on `order_date_ist`; a one-month
query reads one partition (a test checks the plan). Postgres needs the partition key in any unique
key, so the primary key is `(order_id, order_date_ist)`. A default partition catches strays and a
test asserts it stays empty.

**Other choices**
- *Unknown member:* a fact with no dimension row points at `-1` instead of being dropped (for
  example orders cancelled before a rider was assigned use `partner_sk = -1`).
- *PII:* contact details exist only in `gold.customer_pii`; dimensions carry none.
- *Incremental:* a fact loads if gold lacks it or its silver version is newer. A test shows an
  incremental run equals a full rebuild and keeps every surrogate key.
- *`net_revenue`* is computed once, in `fact_orders`: delivered orders only, `order_value -
  discount`, so every report agrees. `daily_city_sales` is rebuilt each run so it cannot drift.
- *Foreign keys* are inline. On an already-loaded large table you would add them `NOT VALID`, then
  `VALIDATE` later without a long lock (Postgres 16 does not allow that on a partitioned child).

**Commands:** `make gold`, `make gold-full`, `make test-04`

**Common mistakes**
- Joining a fact to the *current* customer row, so old orders change city when the customer moves.
- Summing `order_value` after joining orders to items (it repeats once per item line).
- Rebuilding dimensions with new surrogate keys on every run, so facts point at nothing.
- Dropping facts whose dimension is missing instead of using the Unknown member.

**Interview line:** "I model facts at a declared grain, keep history with SCD2 using half-open
validity windows, resolve the dimension version by event date, and partition the big fact by month
so date-bounded queries read one partition."
