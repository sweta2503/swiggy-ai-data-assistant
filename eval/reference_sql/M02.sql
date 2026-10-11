-- Pune cancellation rate: last month, and the month before
WITH t AS (SELECT max(order_date_ist) + 1 AS today FROM gold.fact_orders)
SELECT count(*) FILTER (WHERE f.status = 'cancelled')::numeric / count(*) AS cancellation_rate
FROM gold.fact_orders f JOIN gold.dim_city c USING (city_sk), t
WHERE c.city_name = 'Pune'
  AND f.order_date_ist >= (date_trunc('month', t.today) - interval '2 months')::date
  AND f.order_date_ist < date_trunc('month', t.today)::date
GROUP BY date_trunc('month', f.order_date_ist) ORDER BY date_trunc('month', f.order_date_ist);
