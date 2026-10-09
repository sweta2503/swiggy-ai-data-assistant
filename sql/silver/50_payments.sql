-- Payments. The gateway sends IST with NO zone marker, so we declare it as IST ourselves
-- (AT TIME ZONE 'Asia/Kolkata') to get the true UTC instant. Skip this and every payment looks
-- 5h30m earlier than its order.
DROP TABLE IF EXISTS _payments;
CREATE TEMP TABLE _payments AS
WITH new_rows AS (
    SELECT b.* FROM bronze.payments b
    JOIN _window w ON w.tbl = 'payments' AND b._batch_id > w.lo AND b._batch_id <= w.hi
), ranked AS (
    SELECT n.*,
           coalesce(nullif(trim(payment_id), ''), _file_hash || ':' || _row_number) AS k,
           row_number() OVER (
               PARTITION BY coalesce(nullif(trim(payment_id), ''), _file_hash || ':' || _row_number)
               ORDER BY _ingested_at DESC, _batch_id DESC, _row_number DESC) AS rn
    FROM new_rows n
)
SELECT k, to_jsonb(r) - 'rn' - 'k' AS row_json,
       nullif(trim(payment_id), '') AS payment_id, trim(order_id) AS order_id,
       silver.try_timestamp(payment_ts) AS payment_ts_ist,
       silver.try_timestamp(payment_ts) AT TIME ZONE 'Asia/Kolkata' AS payment_ts_utc,
       lower(trim(method)) AS method, lower(trim(status)) AS status,
       silver.try_numeric(amount) AS amount,
       _source_file, _batch_id, _ingested_at,
       CASE WHEN nullif(trim(payment_id), '') IS NULL THEN 'missing_payment_id'
            WHEN nullif(trim(order_id), '') IS NULL THEN 'missing_order_id'
            WHEN silver.try_timestamp(payment_ts) IS NULL THEN 'bad_payment_ts'
            WHEN silver.try_numeric(amount) IS NULL OR silver.try_numeric(amount) < 0
                 THEN 'bad_amount'
       END AS reject_reason
FROM ranked r WHERE rn = 1;

INSERT INTO silver.rejected_rows (table_name, row_key, row_json, reason)
SELECT 'payments', k, row_json, reject_reason FROM _payments WHERE reject_reason IS NOT NULL
ON CONFLICT DO NOTHING;

INSERT INTO silver.payments AS t
SELECT payment_id, order_id, payment_ts_ist, payment_ts_utc, method, status, amount,
       _source_file, _batch_id, _ingested_at
FROM _payments WHERE reject_reason IS NULL
ON CONFLICT (payment_id) DO UPDATE SET
    order_id = EXCLUDED.order_id, payment_ts_ist = EXCLUDED.payment_ts_ist,
    payment_ts_utc = EXCLUDED.payment_ts_utc, method = EXCLUDED.method, status = EXCLUDED.status,
    amount = EXCLUDED.amount, _source_file = EXCLUDED._source_file,
    _batch_id = EXCLUDED._batch_id, _ingested_at = EXCLUDED._ingested_at
WHERE EXCLUDED._ingested_at >= t._ingested_at;
