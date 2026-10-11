WITH t AS (SELECT max(order_date_ist) + 1 AS today FROM gold.fact_orders)
SELECT sum(f.net_revenue) AS net_revenue FROM gold.fact_orders f, t WHERE f.order_date_ist = t.today - 1;
