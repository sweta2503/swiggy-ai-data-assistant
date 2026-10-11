-- net revenue by the segment the customer had WHEN THEY ORDERED (one row per order, no fan-out)
SELECT cu.segment, sum(f.net_revenue) AS net_revenue
FROM gold.fact_orders f JOIN gold.dim_customer cu USING (customer_sk)
GROUP BY cu.segment ORDER BY 1;
