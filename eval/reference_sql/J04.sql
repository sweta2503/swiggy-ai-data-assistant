-- refunds paid last month, by reason (by the date the refund was paid)
WITH t AS (SELECT max(order_date_ist) + 1 AS today FROM gold.fact_orders)
SELECT r.reason_code, count(*) AS refunds
FROM gold.fact_refunds r, t
WHERE r.refund_date_ist >= (date_trunc('month', t.today) - interval '1 month')::date
  AND r.refund_date_ist < date_trunc('month', t.today)::date
GROUP BY r.reason_code ORDER BY 2 DESC;
