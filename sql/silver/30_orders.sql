-- Orders: the main cleaning job.
--   1. dedupe: keep the newest copy of each order_id
--   2. status: map every spelling to one clean value (unknown spellings are rejected, not guessed)
--   3. time zones: app sends UTC; keep UTC and add the IST wall-clock time and IST business date
--   4. validate: impossible delivery minutes etc. go to rejected_rows with a reason
DROP TABLE IF EXISTS _orders;
CREATE TEMP TABLE _orders AS
WITH new_rows AS (
    SELECT b.* FROM bronze.orders b
    JOIN _window w ON w.tbl = 'orders' AND b._batch_id > w.lo AND b._batch_id <= w.hi
), ranked AS (
    SELECT n.*,
           coalesce(nullif(trim(order_id), ''), _file_hash || ':' || _row_number) AS k,
           row_number() OVER (
               PARTITION BY coalesce(nullif(trim(order_id), ''), _file_hash || ':' || _row_number)
               ORDER BY _ingested_at DESC, _batch_id DESC, _row_number DESC) AS rn
    FROM new_rows n
), typed AS (
    SELECT r.k, to_jsonb(r) - 'rn' - 'k' AS row_json,
           nullif(trim(r.order_id), '') AS order_id,
           trim(r.customer_id) AS customer_id, trim(r.restaurant_id) AS restaurant_id,
           nullif(trim(r.delivery_partner_id), '') AS delivery_partner_id,
           coalesce(nullif(trim(r.city), ''), 'Unknown') AS city,
           nullif(trim(r.city), '') IS NULL AS city_was_null,
           m.status, lower(trim(r.platform)) AS platform,
           silver.try_timestamptz_utc(r.order_ts) AS order_ts_utc,
           silver.try_timestamptz_utc(r.delivered_ts) AS delivered_ts_utc,
           silver.try_numeric(r.delivery_minutes) AS delivery_minutes,
           silver.try_numeric(r.order_value) AS order_value,
           coalesce(silver.try_numeric(r.discount), 0) AS discount,
           coalesce(silver.try_numeric(r.delivery_fee), 0) AS delivery_fee,
           coalesce(silver.try_numeric(r.taxes), 0) AS taxes,
           r._source_file, r._batch_id, r._ingested_at
    FROM ranked r
    LEFT JOIN silver.status_map m ON m.raw_status = lower(trim(r.status))
    WHERE r.rn = 1
)
SELECT t.*,
       t.order_ts_utc AT TIME ZONE 'Asia/Kolkata' AS order_ts_ist,
       CASE WHEN t.order_id IS NULL THEN 'missing_order_id'
            WHEN t.status IS NULL THEN 'unknown_status'
            WHEN t.order_ts_utc IS NULL THEN 'bad_order_ts'
            WHEN t.order_value IS NULL OR t.order_value < 0 THEN 'bad_order_value'
            WHEN t.delivery_minutes IS NOT NULL
                 AND t.delivery_minutes NOT BETWEEN 1 AND 240 THEN 'delivery_minutes_out_of_range'
       END AS reject_reason
FROM typed t;

INSERT INTO silver.rejected_rows (table_name, row_key, row_json, reason)
SELECT 'orders', k, row_json, reject_reason FROM _orders WHERE reject_reason IS NOT NULL
ON CONFLICT DO NOTHING;

INSERT INTO silver.orders AS t
SELECT order_id, customer_id, restaurant_id, delivery_partner_id, city, city_was_null, status,
       platform, order_ts_utc, order_ts_ist, order_ts_ist::date, delivered_ts_utc,
       delivery_minutes::integer, order_value, discount, delivery_fee, taxes,
       _source_file, _batch_id, _ingested_at
FROM _orders WHERE reject_reason IS NULL
ON CONFLICT (order_id) DO UPDATE SET
    customer_id = EXCLUDED.customer_id, restaurant_id = EXCLUDED.restaurant_id,
    delivery_partner_id = EXCLUDED.delivery_partner_id, city = EXCLUDED.city,
    city_was_null = EXCLUDED.city_was_null, status = EXCLUDED.status, platform = EXCLUDED.platform,
    order_ts_utc = EXCLUDED.order_ts_utc, order_ts_ist = EXCLUDED.order_ts_ist,
    order_date_ist = EXCLUDED.order_date_ist, delivered_ts_utc = EXCLUDED.delivered_ts_utc,
    delivery_minutes = EXCLUDED.delivery_minutes, order_value = EXCLUDED.order_value,
    discount = EXCLUDED.discount, delivery_fee = EXCLUDED.delivery_fee, taxes = EXCLUDED.taxes,
    _source_file = EXCLUDED._source_file, _batch_id = EXCLUDED._batch_id,
    _ingested_at = EXCLUDED._ingested_at
WHERE EXCLUDED._ingested_at >= t._ingested_at;
