-- orders with at least one beverage: each order counted ONCE
SELECT count(DISTINCT order_id) AS orders FROM gold.fact_order_items WHERE item_category = 'Beverages';
