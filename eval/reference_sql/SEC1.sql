-- the Bengaluru manager asks for "all cities": the right answer is Bengaluru only
SELECT sum(f.net_revenue) AS net_revenue
FROM gold.fact_orders f JOIN gold.dim_city c USING (city_sk) WHERE c.city_name = 'Bengaluru';
