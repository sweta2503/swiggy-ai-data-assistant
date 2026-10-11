-- average order value = net revenue / delivered orders
SELECT sum(net_revenue) / count(*) FILTER (WHERE status = 'delivered') AS aov FROM gold.fact_orders;
