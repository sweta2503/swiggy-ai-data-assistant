-- cancelled orders / all orders placed
SELECT count(*) FILTER (WHERE status = 'cancelled')::numeric / count(*) AS cancellation_rate
FROM gold.fact_orders;
