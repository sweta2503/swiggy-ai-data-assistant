-- the business day is the IST date: an order at 02:00 IST on 1 March is 20:30 UTC on 28 February
SELECT count(*) AS orders FROM gold.fact_orders WHERE order_date_ist = DATE '2026-03-01';
