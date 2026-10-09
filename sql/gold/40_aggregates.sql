-- Rebuilt in full from fact_orders each run (cheap, and it can never disagree with the fact).
TRUNCATE gold.daily_city_sales;

INSERT INTO gold.daily_city_sales
SELECT order_date_ist, city_sk,
       count(*),
       count(*) FILTER (WHERE status = 'delivered'),
       count(*) FILTER (WHERE status = 'cancelled'),
       count(*) FILTER (WHERE status = 'refunded'),
       count(*) FILTER (WHERE status = 'failed'),
       coalesce(sum(order_value) FILTER (WHERE status = 'delivered'), 0),
       coalesce(sum(discount)    FILTER (WHERE status = 'delivered'), 0),
       coalesce(sum(net_revenue), 0),
       round(avg(delivery_minutes) FILTER (WHERE status = 'delivered'), 2)
FROM gold.fact_orders
GROUP BY order_date_ist, city_sk;
