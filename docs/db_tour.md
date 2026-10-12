# Showing the database on screen

A tour of the warehouse in pgAdmin, in the order that tells the story: raw files become clean tables
become a star schema, and the AI can only see a small, safe slice. Every query here has been run
against this project's database.

## Open it

```bash
make up          # the database (already running if you have used the project)
make pgadmin     # first run downloads the pgAdmin image (about 450 MB), then opens http://localhost:5050
```

There is no sign-in screen (it is a single-user local tool, bound to this machine only). Two servers
are already registered, and each asks for its password once; tick **Save password**:

| Server in the tree | Role | Password (the dev defaults in `.env`) |
|---|---|---|
| Swiggy warehouse (admin: sees everything) | `swiggy_admin` | `POSTGRES_ADMIN_PASSWORD`, `admin_dev_pw` |
| Swiggy as the AI sees it (assistant_ro) | `assistant_ro` | `ASSISTANT_PASSWORD`, `assistant_dev_pw` |

Stop it with `make pgadmin-down`. The database keeps running.

**Prefer to use an app you already have** (pgAdmin desktop, DBeaver, TablePlus)? Connect to host
`localhost`, **port 5433** (not 5432), database `swiggy`, with the user and password above. For a quick
terminal view use `make psql`.

In pgAdmin: expand **Servers > Swiggy warehouse > Databases > swiggy > Schemas**. Right-click a table >
**View/Edit Data** for rows, or open **Tools > Query Tool** and paste the queries below. Right-click a
schema > **ERD For Schema** draws the star schema.

## Part 1: the layers (use the admin server)

**1. One schema per layer.** The `analytics` schema is the only one the AI may read.
```sql
SELECT table_schema, count(*) AS tables
FROM information_schema.tables
WHERE table_schema IN ('bronze', 'silver', 'gold', 'analytics', 'ops')
  AND table_type IN ('BASE TABLE', 'VIEW')
GROUP BY 1 ORDER BY 1;
```

**2. The same orders in three layers.** Bronze keeps duplicates; silver and gold do not.
```sql
SELECT 'bronze (as received)' AS layer, count(*) AS rows FROM bronze.orders
UNION ALL SELECT 'silver (clean)', count(*) FROM silver.orders
UNION ALL SELECT 'gold (star schema)', count(*) FROM gold.fact_orders;
```

**3. Bronze is exactly what arrived.** Look at the messy status spellings that silver maps to four values.
```sql
SELECT status, count(*) AS rows FROM bronze.orders GROUP BY 1 ORDER BY 2 DESC;
SELECT * FROM bronze.orders LIMIT 5;   -- everything is text, plus _file_hash, _batch_id, _ingested_at
```

**4. Silver never drops a row silently.** What it refused, and why.
```sql
SELECT table_name, reason, count(*) FROM silver.rejected_rows GROUP BY 1, 2 ORDER BY 3 DESC;
```

**5. Gold: partitions.** One table per month, so a one-month question reads one partition.
```sql
SELECT inhrelid::regclass AS partition FROM pg_inherits
WHERE inhparent = 'gold.fact_orders'::regclass ORDER BY 1;
```

**6. Gold: SCD Type 2.** A customer who moved has two rows with validity dates.
```sql
SELECT customer_id, city, segment, valid_from, valid_to, is_current
FROM gold.dim_customer
WHERE customer_id IN (SELECT customer_id FROM gold.dim_customer
                      GROUP BY 1 HAVING count(*) > 1 ORDER BY 1 LIMIT 2)
ORDER BY customer_id, valid_from;
```

**7. Data quality.** Forty-five checks; blocking ones can stop a publish.
```sql
SELECT severity, status, count(*) FROM ops.dq_latest_run GROUP BY 1, 2 ORDER BY 1, 2;
```

## Part 2: what the AI is allowed to see (use the assistant_ro server)

**8. Only eight views.**
```sql
SELECT table_name FROM information_schema.tables WHERE table_schema = 'analytics' ORDER BY 1;
```

**9. Everything else is closed.** Each of these fails with `permission denied`.
```sql
SELECT count(*) FROM gold.fact_orders;
SELECT count(*) FROM ops.assistant_runs;
```

**10. City scoping.** With no scope declared the answer is zero rows. Declare one and only those cities appear.
```sql
SELECT count(*) FROM analytics.orders;                  -- 0: no scope, no rows
SET app.allowed_cities = 'Pune';
SELECT count(*), min(city), max(city) FROM analytics.orders;   -- only Pune
SET app.allowed_cities = 'ALL';
SELECT count(*) FROM analytics.orders;                  -- everything
```
(The assistant sets this itself from the user's key. Declaring it by hand here is only possible
because you hold the database login; the application and its guard allow a single `SELECT`.)

## Part 3: the trail (admin server again)

**11. Every question is audited**, with personal data scrubbed and the repairs recorded.
```sql
SELECT ts::timestamp(0), user_id, scope, status, healed, left(question, 50) AS question
FROM ops.assistant_runs ORDER BY ts DESC LIMIT 10;

SELECT a.attempt_no, a.source, a.status, a.error_class, left(a.sql, 70) AS sql
FROM ops.assistant_attempts a
WHERE a.run_id = (SELECT run_id FROM ops.assistant_runs WHERE healed ORDER BY ts DESC LIMIT 1)
ORDER BY a.attempt_no;      -- needs one healed run: ask with DEMO_FAULT_INJECTION=true first
```

**12. The daily run, and the version that retires cached answers.**
```sql
SELECT status, started_at::timestamp(0), data_version_before, data_version_after
FROM ops.pipeline_runs ORDER BY started_at DESC LIMIT 5;
SELECT version, updated_at::timestamp(0) FROM ops.data_version;
```

**13. Schema drift** (after `make bronze` meets a changed file; `make drift-demo` rolls itself back, so
nothing stays here).
```sql
SELECT status, source_file, missing_columns, new_columns FROM ops.schema_alerts;
```

## Suggested order for a video

1. Queries 1 to 3: "this is the raw data, and it is messy."
2. Queries 4 to 6: "this is what the pipeline did about it."
3. Open the chat page and ask a question, then query 11: "and here is the audit record of that question."
4. Switch to the `assistant_ro` server, queries 8 to 10: "this is all the AI can ever touch."
