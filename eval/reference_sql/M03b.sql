-- which of the two cities had the higher net revenue last month
WITH t AS (SELECT max(order_date_ist) + 1 AS today FROM gold.fact_orders)
SELECT c.city_name AS value
FROM gold.fact_orders f JOIN gold.dim_city c USING (city_sk), t
WHERE c.city_name IN ('Bengaluru', 'Mumbai') AND f.order_date_ist >= (date_trunc('month', t.today) - interval '1 month')::date AND f.order_date_ist < date_trunc('month', t.today)::date
GROUP BY c.city_name ORDER BY sum(f.net_revenue) DESC LIMIT 1;
