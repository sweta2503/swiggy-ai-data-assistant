-- for each city, the cuisine with the highest net revenue last month
WITH t AS (SELECT max(order_date_ist) + 1 AS today FROM gold.fact_orders)
SELECT DISTINCT ON (city_name) city_name, cuisine, net_revenue FROM (
  SELECT c.city_name, r.cuisine, sum(f.net_revenue) AS net_revenue
  FROM gold.fact_orders f JOIN gold.dim_city c ON c.city_sk = f.city_sk
  JOIN gold.dim_restaurant r USING (restaurant_sk), t
  WHERE f.order_date_ist >= (date_trunc('month', t.today) - interval '1 month')::date AND f.order_date_ist < date_trunc('month', t.today)::date GROUP BY c.city_name, r.cuisine) x
ORDER BY city_name, net_revenue DESC;
