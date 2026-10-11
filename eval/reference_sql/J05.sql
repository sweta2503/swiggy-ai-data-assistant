-- average order value by city = net revenue / delivered orders
SELECT c.city_name, sum(f.net_revenue) / count(*) FILTER (WHERE f.status = 'delivered') AS aov
FROM gold.fact_orders f JOIN gold.dim_city c USING (city_sk)
GROUP BY c.city_name ORDER BY 1;
