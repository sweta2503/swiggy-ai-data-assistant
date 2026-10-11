SELECT avg(f.delivery_minutes) AS avg_delivery_minutes
FROM gold.fact_orders f JOIN gold.dim_city c USING (city_sk)
WHERE f.status = 'delivered' AND c.city_name = 'Pune';
