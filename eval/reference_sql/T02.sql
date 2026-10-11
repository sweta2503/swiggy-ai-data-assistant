-- orders so far this month (month start up to and including yesterday)
WITH t AS (SELECT max(order_date_ist) + 1 AS today FROM gold.fact_orders)
SELECT count(*) AS orders FROM gold.fact_orders f, t
WHERE f.order_date_ist >= date_trunc('month', t.today)::date AND f.order_date_ist < t.today;
