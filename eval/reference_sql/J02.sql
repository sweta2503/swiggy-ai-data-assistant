SELECT r.cuisine, sum(f.net_revenue) AS net_revenue
FROM gold.fact_orders f JOIN gold.dim_restaurant r USING (restaurant_sk)
GROUP BY r.cuisine ORDER BY 2 DESC LIMIT 1;
