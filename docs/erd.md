# Gold star schema

Key columns are shown, not every column (a test checks each one exists in the database). Facts are events. Dimensions describe who, what, where and when. Every fact row points at one
dimension row through a surrogate key (`*_sk`). `-1` is the "Unknown" member, used when a fact
cannot find its dimension row, so nothing is dropped.

```mermaid
erDiagram
    DIM_DATE ||--o{ FACT_ORDERS : "order_date_ist"
    DIM_DATE ||--o{ FACT_REFUNDS : "refund_date_ist"
    DIM_DATE ||--o{ DAILY_CITY_SALES : "order_date_ist"
    DIM_CITY ||--o{ FACT_ORDERS : city_sk
    DIM_CITY ||--o{ DIM_RESTAURANT : city_sk
    DIM_CITY ||--o{ DIM_PARTNER : city_sk
    DIM_CITY ||--o{ DIM_CUSTOMER : city_sk
    DIM_CITY ||--o{ FACT_REFUNDS : city_sk
    DIM_CITY ||--o{ DAILY_CITY_SALES : city_sk
    DIM_CUSTOMER ||--o{ FACT_ORDERS : customer_sk
    DIM_CUSTOMER ||--o{ FACT_REFUNDS : customer_sk
    DIM_RESTAURANT ||--o{ FACT_ORDERS : restaurant_sk
    DIM_RESTAURANT ||--o{ FACT_ORDER_ITEMS : restaurant_sk
    DIM_RESTAURANT ||--o{ FACT_REFUNDS : restaurant_sk
    DIM_PARTNER ||--o{ FACT_ORDERS : partner_sk
    FACT_ORDERS ||--o{ FACT_ORDER_ITEMS : "order_id + order_date_ist"
    FACT_ORDERS ||--o{ FACT_REFUNDS : "order_id + order_date_ist"
    DIM_CUSTOMER ||--o| CUSTOMER_PII : "customer_id (restricted)"

    FACT_ORDERS {
        text order_id PK "grain: one row per order"
        date order_date_ist PK "partition key (monthly)"
        int customer_sk FK "version valid on the order day"
        int restaurant_sk FK
        int partner_sk FK
        int city_sk FK
        text status
        int delivery_minutes
        numeric order_value
        numeric discount
        numeric net_revenue "delivered: order_value - discount"
    }
    FACT_ORDER_ITEMS {
        text order_item_id PK "grain: one row per item line"
        text order_id FK
        int qty
        numeric unit_price
        numeric line_total
    }
    FACT_REFUNDS {
        text refund_id PK "grain: one row per refund"
        text order_id FK
        date refund_date_ist FK
        text reason_code
        numeric refund_amount
    }
    DAILY_CITY_SALES {
        date order_date_ist PK "grain: one row per day per city"
        int city_sk PK
        int orders
        int delivered_orders
        numeric net_revenue
    }
    DIM_CUSTOMER {
        int customer_sk PK
        text customer_id "SCD Type 2"
        text city
        text segment
        date valid_from "inclusive"
        date valid_to "exclusive, 9999-12-31 if current"
        bool is_current
    }
    DIM_RESTAURANT {
        int restaurant_sk PK
        text restaurant_id
        text cuisine
        int city_sk FK
    }
    DIM_PARTNER {
        int partner_sk PK
        text partner_id
        int city_sk FK
    }
    DIM_CITY {
        int city_sk PK
        text city_name
    }
    DIM_DATE {
        int date_key PK
        date full_date
        bool is_weekend
        bool is_festival
        date week_start_date "Monday"
    }
    CUSTOMER_PII {
        text customer_id PK
        text name
        text phone
        text email
        text address_line "never exposed to the assistant"
    }
```

## Grains (what one row means)

| Table | One row per |
|---|---|
| `fact_orders` | order |
| `fact_order_items` | item line on an order |
| `fact_refunds` | refund |
| `daily_city_sales` | IST day per city |
| `dim_customer` | customer per period with the same city and segment |

**Why grain matters:** summing `order_value` over `fact_order_items` repeats each order's value
once per line. Aggregate items first, or use `fact_orders`. The semantic layer (chapter 6)
carries this warning to the assistant.
