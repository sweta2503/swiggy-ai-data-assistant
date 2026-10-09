# Chapter 04: Gold layer (star schema)

**What we build:** `make gold-full` turns silver into a star schema in about 13 seconds:
three facts, five dimensions, a restricted PII table and a daily aggregate. Diagram:
`docs/erd.md`.

| Table | Grain (one row per...) |
|---|---|
| `fact_orders` | order (149,550 rows) |
| `fact_order_items` | item line (478,181) |
| `fact_refunds` | refund (5,302) |
| `daily_city_sales` | IST day per city (2,190) |
| `dim_customer` | customer per period of constant city + segment (SCD2) |

**Why it matters at work:** analysts and BI tools want one simple shape. Facts hold numbers,
dimensions hold labels, and "revenue by city by month" is one join away.

**SCD Type 2 in one picture.** A customer who moves from Delhi NCR to Pune gets two rows:

| customer_id | city | valid_from | valid_to | is_current |
|---|---|---|---|---|
| CUST000011 | Delhi NCR | 2024-09-05 | 2025-12-16 | false |
| CUST000011 | Pune | 2025-12-16 | 9999-12-31 | true |

Each order points at the version valid *on its date*, so last year's Delhi orders stay Delhi
orders after the move. Windows are half-open (`valid_from <= date < valid_to`), so there is
never a gap or an overlap. The database enforces one current row per customer with a partial
unique index. Surrogate keys never change: re-runs update `valid_to` on the old row (close)
before inserting the new one (open).

**Partitioning.** `fact_orders` is range-partitioned by month on `order_date_ist`. A
one-month query reads one partition (the test checks the plan). Postgres needs the partition
key inside any unique key, so the primary key is `(order_id, order_date_ist)`; items and
refunds reference that pair. A default partition catches strays, and a test asserts it stays
empty.

**Unknown member.** If a fact cannot find its dimension row, it points at `-1` instead of
being dropped. 7,946 orders (cancelled before a rider was assigned, and failed orders) use
`partner_sk = -1`.

**PII.** Contact details exist only in `gold.customer_pii`. Dimensions carry none, and
partner name and phone are left out entirely because no analytics question needs them.

**Loads are incremental.** A fact row loads if gold does not have it, or if its silver
version is newer (`_silver_ingested_at`). Re-running changes nothing; a test shows an
incremental run equals a full rebuild and keeps every surrogate key.

**Foreign keys.** They are inline, so they are enforced from the first row. On a table that
is already loaded and large, the usual pattern is to add them without scanning the table
first and check them later:
`ALTER TABLE t ADD CONSTRAINT fk ... NOT VALID;` then `ALTER TABLE t VALIDATE CONSTRAINT fk;`.
New rows are checked immediately and old rows are checked in the background without a long
lock. (Postgres 16 does not allow `NOT VALID` on a partitioned referencing table, which is
another reason `fact_orders` uses inline keys.)

**Commands:** `make gold`, `make gold-full`, `make test-04`

**Common mistakes**
- Joining a fact to the *current* customer row. Last year's orders then change city when the
  customer moves. Join on the order date.
- Summing `order_value` after joining orders to items (see the grain table).
- Rebuilding dimensions with new surrogate keys on every run. Facts would point at nothing.
- Dropping facts whose dimension row is missing. Use the Unknown member and fix the dimension.

**Design choices**
- `net_revenue` is computed once, in `fact_orders`: delivered orders only, `order_value -
  discount`, no delivery fee or taxes. Every report then agrees.
- The festival calendar is reference data. The demo reads it from the generator's
  `_meta.json`; in real life someone maintains it.
- `daily_city_sales` is rebuilt from `fact_orders` every run so it cannot drift.

**Interview line:** "I model facts at a declared grain, keep history with SCD2 using
half-open validity windows, resolve the dimension version by event date, and partition the
big fact by month so date-bounded queries read one partition."
