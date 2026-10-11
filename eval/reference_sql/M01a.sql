-- the planted cause: the refund reason that grew the most in Mumbai last month, and where
WITH t AS (SELECT max(order_date_ist) + 1 AS today FROM gold.fact_orders),
m AS (
  SELECT r.reason_code,
         count(*) FILTER (WHERE date_trunc('month', r.refund_date_ist) = date_trunc('month', t.today) - interval '1 month')
         - count(*) FILTER (WHERE date_trunc('month', r.refund_date_ist) = date_trunc('month', t.today) - interval '2 months') AS growth
  FROM gold.fact_refunds r JOIN gold.dim_city c USING (city_sk), t
  WHERE c.city_name = 'Mumbai' GROUP BY r.reason_code),
top AS (SELECT reason_code FROM m ORDER BY growth DESC LIMIT 1)
SELECT reason_code AS value FROM top
UNION ALL
(SELECT x.name FROM (SELECT rest.name, count(*) n FROM gold.fact_refunds r
   JOIN gold.dim_restaurant rest USING (restaurant_sk) JOIN gold.dim_city c ON c.city_sk = r.city_sk, t, top
   WHERE c.city_name = 'Mumbai' AND r.reason_code = top.reason_code
     AND r.refund_date_ist >= (date_trunc('month', t.today) - interval '1 month')::date
     AND r.refund_date_ist < date_trunc('month', t.today)::date
   GROUP BY rest.restaurant_id, rest.name ORDER BY n DESC, rest.restaurant_id LIMIT 3) x);
