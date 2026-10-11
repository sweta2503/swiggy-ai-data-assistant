-- delivered orders by customers who were vip WHEN THEY ORDERED, for Mumbai restaurants
SELECT count(*) AS orders
FROM gold.fact_orders f
JOIN gold.dim_customer cu USING (customer_sk) JOIN gold.dim_city c ON c.city_sk = f.city_sk
WHERE f.status = 'delivered' AND cu.segment = 'vip' AND c.city_name = 'Mumbai';
