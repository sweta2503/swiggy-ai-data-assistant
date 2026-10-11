-- delivered orders yesterday = the newest day with data
WITH t AS (SELECT max(order_date_ist) + 1 AS today FROM gold.fact_orders)
SELECT count(*) AS delivered_orders FROM gold.fact_orders f, t
WHERE f.status = 'delivered' AND f.order_date_ist = t.today - 1;
