-- Facts. Each load inserts rows gold does not have yet, and updates rows whose silver version is
-- newer (_silver_ingested_at). Re-running with no new data changes nothing.

SELECT gold.ensure_order_partitions((SELECT min(order_date_ist) FROM silver.orders),
                                    (SELECT max(order_date_ist) FROM silver.orders));

-- ---- fact_orders --------------------------------------------------------------------------
INSERT INTO gold.fact_orders AS t (
    order_id, order_date_ist, order_ts_utc, order_ts_ist, customer_sk, restaurant_sk, partner_sk,
    city_sk, status, platform, delivery_minutes, order_value, discount, delivery_fee, taxes,
    net_revenue, _silver_ingested_at)
SELECT o.order_id, o.order_date_ist, o.order_ts_utc, o.order_ts_ist,
       -- the customer version that was valid ON THE DAY OF THE ORDER (this is the point of SCD2)
       coalesce(c.customer_sk, -1), coalesce(r.restaurant_sk, -1), coalesce(p.partner_sk, -1),
       coalesce(ci.city_sk, -1), o.status, o.platform, o.delivery_minutes, o.order_value,
       o.discount, o.delivery_fee, o.taxes,
       CASE WHEN o.status = 'delivered' THEN o.order_value - o.discount ELSE 0 END,
       o._ingested_at
FROM silver.orders o
LEFT JOIN gold.fact_orders g ON g.order_id = o.order_id
LEFT JOIN gold.dim_customer c ON c.customer_id = o.customer_id
     AND o.order_date_ist >= c.valid_from AND o.order_date_ist < c.valid_to
LEFT JOIN gold.dim_restaurant r ON r.restaurant_id = o.restaurant_id
LEFT JOIN gold.dim_partner p ON p.partner_id = o.delivery_partner_id
LEFT JOIN gold.dim_city ci ON ci.city_name = o.city
WHERE g.order_id IS NULL OR o._ingested_at > g._silver_ingested_at
ON CONFLICT (order_id, order_date_ist) DO UPDATE SET
    order_ts_utc = EXCLUDED.order_ts_utc, order_ts_ist = EXCLUDED.order_ts_ist,
    customer_sk = EXCLUDED.customer_sk, restaurant_sk = EXCLUDED.restaurant_sk,
    partner_sk = EXCLUDED.partner_sk, city_sk = EXCLUDED.city_sk, status = EXCLUDED.status,
    platform = EXCLUDED.platform, delivery_minutes = EXCLUDED.delivery_minutes,
    order_value = EXCLUDED.order_value, discount = EXCLUDED.discount,
    delivery_fee = EXCLUDED.delivery_fee, taxes = EXCLUDED.taxes,
    net_revenue = EXCLUDED.net_revenue, _silver_ingested_at = EXCLUDED._silver_ingested_at;

-- ---- fact_order_items ----------------------------------------------------------------------
INSERT INTO gold.fact_order_items AS t (
    order_item_id, order_id, order_date_ist, restaurant_sk, item_id, item_name, item_category,
    qty, unit_price, line_total, _silver_ingested_at)
SELECT i.order_item_id, i.order_id, o.order_date_ist, o.restaurant_sk, i.item_id, i.item_name,
       m.category, i.qty, i.unit_price, i.line_total, i._ingested_at
FROM silver.order_items i
JOIN gold.fact_orders o ON o.order_id = i.order_id
LEFT JOIN silver.menu_items m ON m.item_id = i.item_id
LEFT JOIN gold.fact_order_items g ON g.order_item_id = i.order_item_id
WHERE g.order_item_id IS NULL OR i._ingested_at > g._silver_ingested_at
ON CONFLICT (order_item_id) DO UPDATE SET
    item_id = EXCLUDED.item_id, item_name = EXCLUDED.item_name,
    item_category = EXCLUDED.item_category, qty = EXCLUDED.qty,
    unit_price = EXCLUDED.unit_price, line_total = EXCLUDED.line_total,
    _silver_ingested_at = EXCLUDED._silver_ingested_at;

-- ---- fact_refunds --------------------------------------------------------------------------
INSERT INTO gold.fact_refunds AS t (
    refund_id, order_id, order_date_ist, refund_date_ist, refund_ts_ist, customer_sk,
    restaurant_sk, city_sk, refund_amount, reason_code, _silver_ingested_at)
SELECT r.refund_id, r.order_id, o.order_date_ist, r.refund_ts_ist::date, r.refund_ts_ist,
       o.customer_sk, o.restaurant_sk, o.city_sk, r.refund_amount, r.reason_code, r._ingested_at
FROM silver.refunds r
JOIN gold.fact_orders o ON o.order_id = r.order_id
LEFT JOIN gold.fact_refunds g ON g.refund_id = r.refund_id
WHERE g.refund_id IS NULL OR r._ingested_at > g._silver_ingested_at
ON CONFLICT (refund_id) DO UPDATE SET
    refund_date_ist = EXCLUDED.refund_date_ist, refund_ts_ist = EXCLUDED.refund_ts_ist,
    refund_amount = EXCLUDED.refund_amount, reason_code = EXCLUDED.reason_code,
    _silver_ingested_at = EXCLUDED._silver_ingested_at;
