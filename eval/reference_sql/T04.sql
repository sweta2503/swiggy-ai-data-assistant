-- orders placed on a Saturday or Sunday last month
WITH t AS (SELECT max(order_date_ist) + 1 AS today FROM gold.fact_orders)
SELECT count(*) AS orders FROM gold.fact_orders f JOIN gold.dim_date d ON d.full_date = f.order_date_ist, t
WHERE d.is_weekend AND f.order_date_ist >= (date_trunc('month', t.today) - interval '1 month')::date AND f.order_date_ist < date_trunc('month', t.today)::date;
