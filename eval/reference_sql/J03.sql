-- top 5 menu items by quantity sold in delivered orders
SELECT i.item_name, sum(i.qty) AS quantity
FROM gold.fact_order_items i
JOIN gold.fact_orders o ON o.order_id = i.order_id AND o.order_date_ist = i.order_date_ist
WHERE o.status = 'delivered'
GROUP BY i.item_name ORDER BY 2 DESC, 1 LIMIT 5;
