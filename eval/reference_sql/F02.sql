-- item sales (before discount) by menu category, delivered orders only
SELECT i.item_category, sum(i.line_total) AS item_sales
FROM gold.fact_order_items i
JOIN gold.fact_orders o ON o.order_id = i.order_id AND o.order_date_ist = i.order_date_ist
WHERE o.status = 'delivered'
GROUP BY i.item_category ORDER BY 1;
