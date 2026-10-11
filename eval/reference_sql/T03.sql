-- net revenue for the month before last and for last month
WITH t AS (SELECT max(order_date_ist) + 1 AS today FROM gold.fact_orders)
SELECT sum(f.net_revenue) AS net_revenue FROM gold.fact_orders f, t
WHERE f.order_date_ist >= (date_trunc('month', t.today) - interval '2 months')::date
  AND f.order_date_ist < date_trunc('month', t.today)::date
GROUP BY date_trunc('month', f.order_date_ist) ORDER BY date_trunc('month', f.order_date_ist);
