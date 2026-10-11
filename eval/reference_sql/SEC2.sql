-- orders last week, Mumbai only
WITH t AS (SELECT max(order_date_ist) + 1 AS today FROM gold.fact_orders)
SELECT count(*) AS orders FROM gold.fact_orders f JOIN gold.dim_city c USING (city_sk), t
WHERE c.city_name = 'Mumbai' AND f.order_date_ist >= date_trunc('week', t.today)::date - 7 AND f.order_date_ist < date_trunc('week', t.today)::date;
