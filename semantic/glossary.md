# Glossary

Plain-English business terms. Each `##` heading is a term; an optional "Also called:" line lists
other names. The assistant is given the entries that match a question.

## Net revenue
Also called: revenue, sales, net sales
The money we keep from delivered orders: items total minus discount. Delivery fees and taxes are
not included, and cancelled, failed and refunded orders count as zero.

## Gross order value
Also called: GMV, gross merchandise value, gross sales
Items total before discount, for delivered orders. Net revenue is this minus discounts.

## AOV
Also called: average order value, basket size
Net revenue divided by the number of delivered orders. How much a typical completed order is worth.

## Delivered order
An order that reached the customer and stayed paid. The base for revenue, AOV and delivery time.

## Cancelled order
An order cancelled before delivery. Counted in "orders placed" but earns no revenue.

## Failed order
An order that did not go through because of a payment or system problem.

## Refunded order
An order that was delivered and then refunded, fully or partly. It has a row in the refunds table
with a reason code and no revenue.

## Cancellation rate
Cancelled orders divided by all orders placed.

## Refund rate
Refunded orders divided by delivered orders.

## Active customer
Also called: active user
A customer with at least one delivered order in the period. A customer who only cancelled is not active.

## New customer
Also called: signup, new user
A customer whose signup date falls in the period. Not the same as a customer's first order.

## Segment
Also called: loyalty tier, customer tier
A customer's loyalty level: new, regular, loyal or vip. In the orders table it is the segment on the
day of the order; in the customers table it is the current one.

## Business date
Also called: order date, IST date
The calendar date in India (IST, UTC+05:30). An order placed at 11 pm UTC belongs to the next day in
India. All "per day", "per week" and "per month" questions use this date.

## Today
Also called: current date, now
The day after the newest order in the data (see analytics.data_as_of). "Yesterday" is the newest day
with data.

## Last week
Also called: previous week
The most recent complete week from Monday to Sunday before the current week.

## Last month
Also called: previous month
The most recent complete calendar month.

## Festival day
A day when order volume spikes. Flagged in the calendar table.

## Monsoon
The rainy season, roughly late June to early September, when deliveries in Mumbai are slower.

## Fan-out
Also called: join duplication, double counting
When joining a table that has many rows per order (such as order items) to one that has one row
per order, the order's money repeats once per row, and sums come out too large. Aggregate the
many-row table first, or avoid the join.

## Spoiled food
A refund reason code (SPOILED_FOOD): the food arrived spoiled. A sudden rise concentrated in a few
restaurants usually points to a quality or storage problem at those restaurants.
