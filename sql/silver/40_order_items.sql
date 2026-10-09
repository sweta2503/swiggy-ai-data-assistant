-- Order items. A line must belong to an order that made it into silver.
--   parent never seen at all          -> 'orphan'
--   parent seen but rejected (outlier) -> 'parent_order_rejected'
-- Orphans are re-checked on every run: if the order arrives late, the line is promoted.
DROP TABLE IF EXISTS _items;
CREATE TEMP TABLE _items AS
WITH new_rows AS (
    SELECT b.* FROM bronze.order_items b
    JOIN _window w ON w.tbl = 'order_items' AND b._batch_id > w.lo AND b._batch_id <= w.hi
    UNION ALL
    SELECT b.* FROM bronze.order_items b
    JOIN _window w ON w.tbl = 'order_items' AND b._batch_id <= w.lo
    WHERE trim(b.order_item_id) IN (SELECT row_key FROM silver.rejected_rows
                                    WHERE table_name = 'order_items' AND reason = 'orphan')
), ranked AS (
    SELECT n.*,
           coalesce(nullif(trim(order_item_id), ''), _file_hash || ':' || _row_number) AS k,
           row_number() OVER (
               PARTITION BY coalesce(nullif(trim(order_item_id), ''), _file_hash || ':' || _row_number)
               ORDER BY _ingested_at DESC, _batch_id DESC, _row_number DESC) AS rn
    FROM new_rows n
), typed AS (
    SELECT r.k, to_jsonb(r) - 'rn' - 'k' AS row_json,
           nullif(trim(r.order_item_id), '') AS order_item_id, trim(r.order_id) AS order_id,
           trim(r.item_id) AS item_id, trim(r.item_name) AS item_name,
           silver.try_numeric(r.qty) AS qty, silver.try_numeric(r.unit_price) AS unit_price,
           EXISTS (SELECT 1 FROM silver.orders o WHERE o.order_id = trim(r.order_id)) AS parent_ok,
           EXISTS (SELECT 1 FROM bronze.orders o WHERE o.order_id = trim(r.order_id)) AS parent_seen,
           r._source_file, r._batch_id, r._ingested_at
    FROM ranked r WHERE r.rn = 1
)
SELECT t.*,
       CASE WHEN t.order_item_id IS NULL THEN 'missing_order_item_id'
            WHEN t.qty IS NULL OR t.qty < 1 OR t.qty <> trunc(t.qty) THEN 'bad_qty'
            WHEN t.unit_price IS NULL OR t.unit_price < 0 THEN 'bad_unit_price'
            WHEN NOT t.parent_ok AND t.parent_seen THEN 'parent_order_rejected'
            WHEN NOT t.parent_ok THEN 'orphan'
       END AS reject_reason
FROM typed t;

INSERT INTO silver.rejected_rows (table_name, row_key, row_json, reason)
SELECT 'order_items', k, row_json, reject_reason FROM _items WHERE reject_reason IS NOT NULL
ON CONFLICT DO NOTHING;

INSERT INTO silver.order_items AS t (order_item_id, order_id, item_id, item_name, qty, unit_price,
                                     _source_file, _batch_id, _ingested_at)
SELECT order_item_id, order_id, item_id, item_name, qty::integer, unit_price,
       _source_file, _batch_id, _ingested_at
FROM _items WHERE reject_reason IS NULL
ON CONFLICT (order_item_id) DO UPDATE SET
    order_id = EXCLUDED.order_id, item_id = EXCLUDED.item_id, item_name = EXCLUDED.item_name,
    qty = EXCLUDED.qty, unit_price = EXCLUDED.unit_price, _source_file = EXCLUDED._source_file,
    _batch_id = EXCLUDED._batch_id, _ingested_at = EXCLUDED._ingested_at
WHERE EXCLUDED._ingested_at >= t._ingested_at;

-- a promoted line is no longer an orphan
DELETE FROM silver.rejected_rows r USING silver.order_items s
WHERE r.table_name = 'order_items' AND r.reason = 'orphan' AND r.row_key = s.order_item_id;
