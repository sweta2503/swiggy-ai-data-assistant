-- orders placed last week (the Monday-to-Sunday week before the current one)
WITH t AS (SELECT max(order_date_ist) + 1 AS today FROM gold.fact_orders)
SELECT count(*) AS orders FROM gold.fact_orders f, t WHERE f.order_date_ist >= date_trunc('week', t.today)::date - 7 AND f.order_date_ist < date_trunc('week', t.today)::date;
