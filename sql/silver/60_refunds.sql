-- Refunds. Same parent rule as order items: a refund needs an order that is in silver.
-- Refunds whose order has not arrived yet are held as 'orphan' and re-checked every run.
DROP TABLE IF EXISTS _refunds;
CREATE TEMP TABLE _refunds AS
WITH new_rows AS (
    SELECT b.* FROM bronze.refunds b
    JOIN _window w ON w.tbl = 'refunds' AND b._batch_id > w.lo AND b._batch_id <= w.hi
    UNION ALL
    SELECT b.* FROM bronze.refunds b
    JOIN _window w ON w.tbl = 'refunds' AND b._batch_id <= w.lo
    WHERE trim(b.refund_id) IN (SELECT row_key FROM silver.rejected_rows
                                WHERE table_name = 'refunds' AND reason = 'orphan')
), ranked AS (
    SELECT n.*,
           coalesce(nullif(trim(refund_id), ''), _file_hash || ':' || _row_number) AS k,
           row_number() OVER (
               PARTITION BY coalesce(nullif(trim(refund_id), ''), _file_hash || ':' || _row_number)
               ORDER BY _ingested_at DESC, _batch_id DESC, _row_number DESC) AS rn
    FROM new_rows n
), typed AS (
    SELECT r.k, to_jsonb(r) - 'rn' - 'k' AS row_json,
           nullif(trim(r.refund_id), '') AS refund_id, trim(r.order_id) AS order_id,
           silver.try_timestamptz_utc(r.refund_ts) AS refund_ts_utc,
           silver.try_numeric(r.refund_amount) AS refund_amount,
           upper(trim(r.reason_code)) AS reason_code,
           EXISTS (SELECT 1 FROM silver.orders o WHERE o.order_id = trim(r.order_id)) AS parent_ok,
           EXISTS (SELECT 1 FROM bronze.orders o WHERE o.order_id = trim(r.order_id)) AS parent_seen,
           r._source_file, r._batch_id, r._ingested_at
    FROM ranked r WHERE r.rn = 1
)
SELECT t.*, t.refund_ts_utc AT TIME ZONE 'Asia/Kolkata' AS refund_ts_ist,
       CASE WHEN t.refund_id IS NULL THEN 'missing_refund_id'
            WHEN t.refund_ts_utc IS NULL THEN 'bad_refund_ts'
            WHEN t.refund_amount IS NULL OR t.refund_amount < 0 THEN 'bad_refund_amount'
            WHEN NOT t.parent_ok AND t.parent_seen THEN 'parent_order_rejected'
            WHEN NOT t.parent_ok THEN 'orphan'
       END AS reject_reason
FROM typed t;

INSERT INTO silver.rejected_rows (table_name, row_key, row_json, reason)
SELECT 'refunds', k, row_json, reject_reason FROM _refunds WHERE reject_reason IS NOT NULL
ON CONFLICT DO NOTHING;

INSERT INTO silver.refunds AS t
SELECT refund_id, order_id, refund_ts_utc, refund_ts_ist, refund_amount, reason_code,
       _source_file, _batch_id, _ingested_at
FROM _refunds WHERE reject_reason IS NULL
ON CONFLICT (refund_id) DO UPDATE SET
    order_id = EXCLUDED.order_id, refund_ts_utc = EXCLUDED.refund_ts_utc,
    refund_ts_ist = EXCLUDED.refund_ts_ist, refund_amount = EXCLUDED.refund_amount,
    reason_code = EXCLUDED.reason_code, _source_file = EXCLUDED._source_file,
    _batch_id = EXCLUDED._batch_id, _ingested_at = EXCLUDED._ingested_at
WHERE EXCLUDED._ingested_at >= t._ingested_at;

DELETE FROM silver.rejected_rows r USING silver.refunds s
WHERE r.table_name = 'refunds' AND r.reason = 'orphan' AND r.row_key = s.refund_id;
