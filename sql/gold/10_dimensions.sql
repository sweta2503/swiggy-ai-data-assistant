-- Load the simple dimensions. Each is an upsert on its natural key, so surrogate keys never
-- change once handed out (facts depend on them).

-- ---- "Unknown" members: where a fact points when its dimension row is missing ---------------
INSERT INTO gold.dim_city (city_sk, city_name) OVERRIDING SYSTEM VALUE
VALUES (-1, 'Unknown') ON CONFLICT DO NOTHING;

INSERT INTO gold.dim_restaurant (restaurant_sk, restaurant_id, name, cuisine, city_sk)
OVERRIDING SYSTEM VALUE VALUES (-1, 'UNKNOWN', 'Unknown restaurant', 'Unknown', -1)
ON CONFLICT DO NOTHING;

INSERT INTO gold.dim_partner (partner_sk, partner_id, city_sk) OVERRIDING SYSTEM VALUE
VALUES (-1, 'UNKNOWN', -1) ON CONFLICT DO NOTHING;   -- also used for "not assigned yet"

INSERT INTO gold.dim_customer (customer_sk, customer_id, city_sk, city, segment, valid_from,
                               valid_to, is_current)
OVERRIDING SYSTEM VALUE
VALUES (-1, 'UNKNOWN', -1, 'Unknown', 'new', DATE '1900-01-01', DATE '9999-12-31', true)
ON CONFLICT DO NOTHING;

-- ---- cities -------------------------------------------------------------------------------
INSERT INTO gold.dim_city (city_name)
SELECT DISTINCT city FROM (
    SELECT city FROM silver.restaurants
    UNION SELECT city FROM silver.delivery_partners
    UNION SELECT city FROM silver.orders
    UNION SELECT city FROM silver.customer_snapshots
) c WHERE city IS NOT NULL
ON CONFLICT (city_name) DO NOTHING;

-- ---- restaurants (changes overwrite: SCD Type 1) -------------------------------------------
INSERT INTO gold.dim_restaurant AS d (restaurant_id, name, cuisine, city_sk, rating,
                                      onboarded_date, is_active)
SELECT r.restaurant_id, r.name, r.cuisine, c.city_sk, r.rating, r.onboarded_date, r.is_active
FROM silver.restaurants r JOIN gold.dim_city c ON c.city_name = r.city
ON CONFLICT (restaurant_id) DO UPDATE SET
    name = EXCLUDED.name, cuisine = EXCLUDED.cuisine, city_sk = EXCLUDED.city_sk,
    rating = EXCLUDED.rating, onboarded_date = EXCLUDED.onboarded_date,
    is_active = EXCLUDED.is_active;

-- ---- delivery partners ----------------------------------------------------------------------
INSERT INTO gold.dim_partner (partner_id, city_sk, joined_date)
SELECT p.partner_id, c.city_sk, p.joined_date
FROM silver.delivery_partners p JOIN gold.dim_city c ON c.city_name = p.city
ON CONFLICT (partner_id) DO UPDATE SET city_sk = EXCLUDED.city_sk,
                                       joined_date = EXCLUDED.joined_date;

-- ---- calendar --------------------------------------------------------------------------------
-- Covers the first order date to 60 days after the last, so refunds always find their day.
INSERT INTO gold.dim_date (date_key, full_date, day_name, day_of_week, is_weekend,
                           week_start_date, month_start_date, month_name, quarter, year)
SELECT to_char(d, 'YYYYMMDD')::int, d, trim(to_char(d, 'Day')), extract(isodow FROM d)::int,
       extract(isodow FROM d) >= 6, date_trunc('week', d)::date, date_trunc('month', d)::date,
       trim(to_char(d, 'Month')), extract(quarter FROM d)::int, extract(year FROM d)::int
FROM generate_series(
        (SELECT min(order_date_ist) FROM silver.orders),
        (SELECT max(order_date_ist) FROM silver.orders) + 60, interval '1 day') AS g(d)
ON CONFLICT (date_key) DO NOTHING;

UPDATE gold.dim_date d
SET is_festival = EXISTS (SELECT 1 FROM gold.festival_calendar f WHERE f.festival_date = d.full_date),
    festival_name = (SELECT f.festival_name FROM gold.festival_calendar f
                     WHERE f.festival_date = d.full_date)
WHERE d.is_festival IS DISTINCT FROM
          EXISTS (SELECT 1 FROM gold.festival_calendar f WHERE f.festival_date = d.full_date)
   OR d.festival_name IS DISTINCT FROM
          (SELECT f.festival_name FROM gold.festival_calendar f WHERE f.festival_date = d.full_date);
