-- Ratings: one per order, stars must be 1..5.
DROP TABLE IF EXISTS _ratings;
CREATE TEMP TABLE _ratings AS
WITH new_rows AS (
    SELECT b.* FROM bronze.ratings b
    JOIN _window w ON w.tbl = 'ratings' AND b._batch_id > w.lo AND b._batch_id <= w.hi
), ranked AS (
    SELECT n.*,
           coalesce(nullif(trim(order_id), ''), _file_hash || ':' || _row_number) AS k,
           row_number() OVER (
               PARTITION BY coalesce(nullif(trim(order_id), ''), _file_hash || ':' || _row_number)
               ORDER BY _ingested_at DESC, _batch_id DESC, _row_number DESC) AS rn
    FROM new_rows n
)
SELECT k, to_jsonb(r) - 'rn' - 'k' AS row_json,
       nullif(trim(order_id), '') AS order_id,
       silver.try_numeric(restaurant_rating) AS restaurant_rating,
       silver.try_numeric(delivery_rating) AS delivery_rating,
       silver.try_timestamptz_utc(rated_ts) AS rated_ts_utc,
       _source_file, _batch_id, _ingested_at,
       CASE WHEN nullif(trim(order_id), '') IS NULL THEN 'missing_order_id'
            WHEN silver.try_numeric(restaurant_rating) IS NULL
                 OR silver.try_numeric(restaurant_rating) NOT BETWEEN 1 AND 5
                 OR silver.try_numeric(delivery_rating) IS NULL
                 OR silver.try_numeric(delivery_rating) NOT BETWEEN 1 AND 5 THEN 'bad_rating'
            WHEN silver.try_timestamptz_utc(rated_ts) IS NULL THEN 'bad_rated_ts'
       END AS reject_reason
FROM ranked r WHERE rn = 1;

INSERT INTO silver.rejected_rows (table_name, row_key, row_json, reason)
SELECT 'ratings', k, row_json, reject_reason FROM _ratings WHERE reject_reason IS NOT NULL
ON CONFLICT DO NOTHING;

INSERT INTO silver.ratings AS t
SELECT order_id, restaurant_rating::integer, delivery_rating::integer, rated_ts_utc,
       _source_file, _batch_id, _ingested_at
FROM _ratings WHERE reject_reason IS NULL
ON CONFLICT (order_id) DO UPDATE SET
    restaurant_rating = EXCLUDED.restaurant_rating, delivery_rating = EXCLUDED.delivery_rating,
    rated_ts_utc = EXCLUDED.rated_ts_utc, _source_file = EXCLUDED._source_file,
    _batch_id = EXCLUDED._batch_id, _ingested_at = EXCLUDED._ingested_at
WHERE EXCLUDED._ingested_at >= t._ingested_at;
