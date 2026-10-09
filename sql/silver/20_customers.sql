-- Customer snapshots. The key is (customer_id, snapshot_date); gold builds SCD2 from these.
-- PII (name, phone, email, address) is still here; gold moves it to a restricted table.
DROP TABLE IF EXISTS _customers;
CREATE TEMP TABLE _customers AS
WITH new_rows AS (
    SELECT b.* FROM bronze.customer_snapshots b
    JOIN _window w ON w.tbl = 'customer_snapshots' AND b._batch_id > w.lo AND b._batch_id <= w.hi
), ranked AS (
    SELECT n.*,
           coalesce(nullif(trim(customer_id), ''), _file_hash || ':' || _row_number)
               || '|' || coalesce(trim(snapshot_date), '') AS k,
           row_number() OVER (
               PARTITION BY coalesce(nullif(trim(customer_id), ''), _file_hash || ':' || _row_number),
                            trim(snapshot_date)
               ORDER BY _ingested_at DESC, _batch_id DESC, _row_number DESC) AS rn
    FROM new_rows n
)
SELECT k, to_jsonb(r) - 'rn' - 'k' AS row_json,
       trim(customer_id) AS customer_id, silver.try_date(snapshot_date) AS snapshot_date,
       trim(name) AS name, trim(phone) AS phone, lower(trim(email)) AS email,
       trim(address_line) AS address_line, trim(pincode) AS pincode,
       coalesce(nullif(trim(city), ''), 'Unknown') AS city,
       nullif(trim(city), '') IS NULL AS city_was_null,
       lower(trim(segment)) AS segment, silver.try_date(signup_date) AS signup_date,
       _source_file, _batch_id, _ingested_at,
       CASE WHEN nullif(trim(customer_id), '') IS NULL THEN 'missing_customer_id'
            WHEN silver.try_date(snapshot_date) IS NULL THEN 'bad_snapshot_date'
            WHEN lower(trim(segment)) NOT IN ('new', 'regular', 'loyal', 'vip')
                 OR segment IS NULL THEN 'unknown_segment'
       END AS reject_reason
FROM ranked r WHERE rn = 1;

INSERT INTO silver.rejected_rows (table_name, row_key, row_json, reason)
SELECT 'customer_snapshots', k, row_json, reject_reason FROM _customers
WHERE reject_reason IS NOT NULL
ON CONFLICT DO NOTHING;

INSERT INTO silver.customer_snapshots AS t
SELECT customer_id, snapshot_date, name, phone, email, address_line, pincode, city, city_was_null,
       segment, signup_date, _source_file, _batch_id, _ingested_at
FROM _customers WHERE reject_reason IS NULL
ON CONFLICT (customer_id, snapshot_date) DO UPDATE SET
    name = EXCLUDED.name, phone = EXCLUDED.phone, email = EXCLUDED.email,
    address_line = EXCLUDED.address_line, pincode = EXCLUDED.pincode, city = EXCLUDED.city,
    city_was_null = EXCLUDED.city_was_null, segment = EXCLUDED.segment,
    signup_date = EXCLUDED.signup_date, _source_file = EXCLUDED._source_file,
    _batch_id = EXCLUDED._batch_id, _ingested_at = EXCLUDED._ingested_at
WHERE EXCLUDED._ingested_at >= t._ingested_at;
