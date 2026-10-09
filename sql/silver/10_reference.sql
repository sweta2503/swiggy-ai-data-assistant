-- Reference data: restaurants, delivery partners, menu items.
-- Pattern used by every silver file:
--   new rows -> dedupe (newest wins) -> cast + validate -> rejects to rejected_rows -> upsert clean rows

-- ---- restaurants -------------------------------------------------------------------------
DROP TABLE IF EXISTS _restaurants;
CREATE TEMP TABLE _restaurants AS
WITH new_rows AS (
    SELECT b.* FROM bronze.restaurants b
    JOIN _window w ON w.tbl = 'restaurants' AND b._batch_id > w.lo AND b._batch_id <= w.hi
), ranked AS (
    SELECT n.*,
           coalesce(nullif(trim(restaurant_id), ''), _file_hash || ':' || _row_number) AS k,
           row_number() OVER (
               PARTITION BY coalesce(nullif(trim(restaurant_id), ''), _file_hash || ':' || _row_number)
               ORDER BY _ingested_at DESC, _batch_id DESC, _row_number DESC) AS rn
    FROM new_rows n
)
SELECT k, to_jsonb(r) - 'rn' - 'k' AS row_json,
       trim(restaurant_id) AS restaurant_id, trim(name) AS name,
       coalesce(nullif(trim(cuisine), ''), 'Unknown') AS cuisine,
       nullif(trim(cuisine), '') IS NULL AS cuisine_was_blank,
       coalesce(nullif(trim(city), ''), 'Unknown') AS city,
       silver.try_numeric(rating) AS rating,
       silver.try_date(onboarded_date) AS onboarded_date,
       lower(trim(is_active)) = 'true' AS is_active,
       _source_file, _batch_id, _ingested_at,
       CASE WHEN nullif(trim(restaurant_id), '') IS NULL THEN 'missing_restaurant_id'
            WHEN nullif(trim(name), '') IS NULL THEN 'missing_name' END AS reject_reason
FROM ranked r WHERE rn = 1;

INSERT INTO silver.rejected_rows (table_name, row_key, row_json, reason)
SELECT 'restaurants', k, row_json, reject_reason FROM _restaurants WHERE reject_reason IS NOT NULL
ON CONFLICT DO NOTHING;

INSERT INTO silver.restaurants AS t
SELECT restaurant_id, name, cuisine, cuisine_was_blank, city, rating, onboarded_date, is_active,
       _source_file, _batch_id, _ingested_at
FROM _restaurants WHERE reject_reason IS NULL
ON CONFLICT (restaurant_id) DO UPDATE SET
    name = EXCLUDED.name, cuisine = EXCLUDED.cuisine, cuisine_was_blank = EXCLUDED.cuisine_was_blank,
    city = EXCLUDED.city, rating = EXCLUDED.rating, onboarded_date = EXCLUDED.onboarded_date,
    is_active = EXCLUDED.is_active, _source_file = EXCLUDED._source_file,
    _batch_id = EXCLUDED._batch_id, _ingested_at = EXCLUDED._ingested_at
WHERE EXCLUDED._ingested_at >= t._ingested_at;

-- ---- delivery partners -------------------------------------------------------------------
DROP TABLE IF EXISTS _partners;
CREATE TEMP TABLE _partners AS
WITH new_rows AS (
    SELECT b.* FROM bronze.delivery_partners b
    JOIN _window w ON w.tbl = 'delivery_partners' AND b._batch_id > w.lo AND b._batch_id <= w.hi
), ranked AS (
    SELECT n.*,
           coalesce(nullif(trim(partner_id), ''), _file_hash || ':' || _row_number) AS k,
           row_number() OVER (
               PARTITION BY coalesce(nullif(trim(partner_id), ''), _file_hash || ':' || _row_number)
               ORDER BY _ingested_at DESC, _batch_id DESC, _row_number DESC) AS rn
    FROM new_rows n
)
SELECT k, to_jsonb(r) - 'rn' - 'k' AS row_json,
       trim(partner_id) AS partner_id, trim(name) AS name, trim(phone) AS phone,
       coalesce(nullif(trim(city), ''), 'Unknown') AS city,
       silver.try_date(joined_date) AS joined_date,
       _source_file, _batch_id, _ingested_at,
       CASE WHEN nullif(trim(partner_id), '') IS NULL THEN 'missing_partner_id'
            WHEN nullif(trim(name), '') IS NULL THEN 'missing_name' END AS reject_reason
FROM ranked r WHERE rn = 1;

INSERT INTO silver.rejected_rows (table_name, row_key, row_json, reason)
SELECT 'delivery_partners', k, row_json, reject_reason FROM _partners WHERE reject_reason IS NOT NULL
ON CONFLICT DO NOTHING;

INSERT INTO silver.delivery_partners AS t
SELECT partner_id, name, phone, city, joined_date, _source_file, _batch_id, _ingested_at
FROM _partners WHERE reject_reason IS NULL
ON CONFLICT (partner_id) DO UPDATE SET
    name = EXCLUDED.name, phone = EXCLUDED.phone, city = EXCLUDED.city,
    joined_date = EXCLUDED.joined_date, _source_file = EXCLUDED._source_file,
    _batch_id = EXCLUDED._batch_id, _ingested_at = EXCLUDED._ingested_at
WHERE EXCLUDED._ingested_at >= t._ingested_at;

-- ---- menu items --------------------------------------------------------------------------
DROP TABLE IF EXISTS _menu;
CREATE TEMP TABLE _menu AS
WITH new_rows AS (
    SELECT b.* FROM bronze.menu_items b
    JOIN _window w ON w.tbl = 'menu_items' AND b._batch_id > w.lo AND b._batch_id <= w.hi
), ranked AS (
    SELECT n.*,
           coalesce(nullif(trim(item_id), ''), _file_hash || ':' || _row_number) AS k,
           row_number() OVER (
               PARTITION BY coalesce(nullif(trim(item_id), ''), _file_hash || ':' || _row_number)
               ORDER BY _ingested_at DESC, _batch_id DESC, _row_number DESC) AS rn
    FROM new_rows n
)
SELECT k, to_jsonb(r) - 'rn' - 'k' AS row_json,
       trim(item_id) AS item_id, trim(restaurant_id) AS restaurant_id, trim(item_name) AS item_name,
       nullif(trim(category), '') AS category, silver.try_numeric(price) AS price,
       _source_file, _batch_id, _ingested_at,
       CASE WHEN nullif(trim(item_id), '') IS NULL THEN 'missing_item_id'
            WHEN nullif(trim(restaurant_id), '') IS NULL THEN 'missing_restaurant_id'
            WHEN nullif(trim(item_name), '') IS NULL THEN 'missing_item_name'
            WHEN silver.try_numeric(price) IS NULL OR silver.try_numeric(price) < 0 THEN 'bad_price'
       END AS reject_reason
FROM ranked r WHERE rn = 1;

INSERT INTO silver.rejected_rows (table_name, row_key, row_json, reason)
SELECT 'menu_items', k, row_json, reject_reason FROM _menu WHERE reject_reason IS NOT NULL
ON CONFLICT DO NOTHING;

INSERT INTO silver.menu_items AS t
SELECT item_id, restaurant_id, item_name, category, price, _source_file, _batch_id, _ingested_at
FROM _menu WHERE reject_reason IS NULL
ON CONFLICT (item_id) DO UPDATE SET
    restaurant_id = EXCLUDED.restaurant_id, item_name = EXCLUDED.item_name,
    category = EXCLUDED.category, price = EXCLUDED.price, _source_file = EXCLUDED._source_file,
    _batch_id = EXCLUDED._batch_id, _ingested_at = EXCLUDED._ingested_at
WHERE EXCLUDED._ingested_at >= t._ingested_at;
