-- top 5 restaurants by net revenue in Bengaluru last month
WITH t AS (SELECT max(order_date_ist) + 1 AS today FROM gold.fact_orders)
SELECT r.name AS restaurant_name, sum(f.net_revenue) AS net_revenue
FROM gold.fact_orders f
JOIN gold.dim_restaurant r USING (restaurant_sk) JOIN gold.dim_city c ON c.city_sk = f.city_sk, t
WHERE c.city_name = 'Bengaluru' AND f.order_date_ist >= (date_trunc('month', t.today) - interval '1 month')::date AND f.order_date_ist < date_trunc('month', t.today)::date
GROUP BY r.restaurant_id, r.name ORDER BY 2 DESC, r.restaurant_id LIMIT 5;
