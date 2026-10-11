-- net revenue last month for Bengaluru and for Mumbai
WITH t AS (SELECT max(order_date_ist) + 1 AS today FROM gold.fact_orders)
SELECT sum(f.net_revenue) AS net_revenue
FROM gold.fact_orders f JOIN gold.dim_city c USING (city_sk), t
WHERE c.city_name IN ('Bengaluru', 'Mumbai') AND f.order_date_ist >= (date_trunc('month', t.today) - interval '1 month')::date AND f.order_date_ist < date_trunc('month', t.today)::date
GROUP BY c.city_name ORDER BY c.city_name;
