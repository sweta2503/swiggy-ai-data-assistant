-- The ONLY tables the assistant will ever be allowed to read: flat, friendly views over gold.
--   * names, not surrogate keys (city, restaurant_name ...), so questions need few joins
--   * no personal data: no names, emails, addresses; phone only as a masked form
--   * dates are IST business dates
-- Phase 7 adds the roles, row-level security and grants around these views.

CREATE OR REPLACE VIEW analytics.orders AS
SELECT f.order_id,
       f.order_date_ist              AS order_date,
       f.order_ts_ist                AS order_ts,
       city.city_name                AS city,
       r.restaurant_id,
       r.name                        AS restaurant_name,
       r.cuisine,
       cu.customer_id,
       cu.segment                    AS customer_segment,
       cu.city                       AS customer_city,
       f.status,
       f.platform,
       f.delivery_minutes,
       f.order_value,
       f.discount,
       f.delivery_fee,
       f.taxes,
       f.net_revenue
FROM gold.fact_orders f
JOIN gold.dim_city city       ON city.city_sk = f.city_sk
JOIN gold.dim_restaurant r    ON r.restaurant_sk = f.restaurant_sk
JOIN gold.dim_customer cu     ON cu.customer_sk = f.customer_sk;

CREATE OR REPLACE VIEW analytics.order_items AS
SELECT i.order_item_id,
       i.order_id,
       i.order_date_ist              AS order_date,
       o.status                      AS order_status,
       city.city_name                AS city,
       r.restaurant_id,
       r.name                        AS restaurant_name,
       r.cuisine,
       i.item_name,
       i.item_category,
       i.qty,
       i.unit_price,
       i.line_total
FROM gold.fact_order_items i
JOIN gold.fact_orders o       ON o.order_id = i.order_id AND o.order_date_ist = i.order_date_ist
JOIN gold.dim_restaurant r    ON r.restaurant_sk = i.restaurant_sk
JOIN gold.dim_city city       ON city.city_sk = o.city_sk;

CREATE OR REPLACE VIEW analytics.refunds AS
SELECT f.refund_id,
       f.order_id,
       f.order_date_ist              AS order_date,
       f.refund_date_ist             AS refund_date,
       city.city_name                AS city,
       r.restaurant_id,
       r.name                        AS restaurant_name,
       r.cuisine,
       f.reason_code,
       f.refund_amount
FROM gold.fact_refunds f
JOIN gold.dim_restaurant r    ON r.restaurant_sk = f.restaurant_sk
JOIN gold.dim_city city       ON city.city_sk = f.city_sk;

-- One row per customer (their current state). Contact details appear only as a masked phone.
CREATE OR REPLACE VIEW analytics.customers AS
SELECT cu.customer_id,
       cu.city,
       cu.segment,
       (SELECT min(h.valid_from) FROM gold.dim_customer h
        WHERE h.customer_id = cu.customer_id)        AS signup_date,
       'XXXXXX' || right(p.phone, 4)                 AS masked_phone
FROM gold.dim_customer cu
LEFT JOIN gold.customer_pii p ON p.customer_id = cu.customer_id
WHERE cu.is_current AND cu.customer_id <> 'UNKNOWN';

CREATE OR REPLACE VIEW analytics.restaurants AS
SELECT r.restaurant_id,
       r.name                        AS restaurant_name,
       r.cuisine,
       city.city_name                AS city,
       r.rating,
       r.onboarded_date,
       r.is_active
FROM gold.dim_restaurant r
JOIN gold.dim_city city ON city.city_sk = r.city_sk
WHERE r.restaurant_id <> 'UNKNOWN';

CREATE OR REPLACE VIEW analytics.calendar AS
SELECT full_date AS date, day_name, day_of_week, is_weekend, week_start_date, month_start_date,
       month_name, quarter, year, is_festival, festival_name
FROM gold.dim_date;

CREATE OR REPLACE VIEW analytics.daily_city_sales AS
SELECT d.order_date_ist              AS order_date,
       city.city_name                AS city,
       d.orders, d.delivered_orders, d.cancelled_orders, d.refunded_orders, d.failed_orders,
       d.gross_order_value, d.discount_total, d.net_revenue, d.avg_delivery_minutes
FROM gold.daily_city_sales d
JOIN gold.dim_city city ON city.city_sk = d.city_sk;

-- "Today" for relative dates ("last week"). It is the day after the newest order, so the
-- answer is the same whether the data was loaded this morning or last month.
CREATE OR REPLACE VIEW analytics.data_as_of AS
SELECT max(order_date_ist) AS latest_order_date,
       max(order_date_ist) + 1 AS today
FROM gold.fact_orders;
