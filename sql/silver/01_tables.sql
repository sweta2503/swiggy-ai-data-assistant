-- Silver: clean, typed, deduplicated. One row per business key.
-- Every silver row keeps its lineage (_source_file, _batch_id, _ingested_at).

-- Spelling -> clean status. Keys are lower case; lookups use lower(trim(raw)).
CREATE TABLE IF NOT EXISTS silver.status_map (
    raw_status text PRIMARY KEY,
    status     text NOT NULL CHECK (status IN ('delivered', 'cancelled', 'refunded', 'failed'))
);
INSERT INTO silver.status_map VALUES
    ('delivered', 'delivered'), ('dlvrd', 'delivered'),
    ('cancelled', 'cancelled'), ('canceled', 'cancelled'),
    ('refunded', 'refunded'), ('failed', 'failed')
ON CONFLICT DO NOTHING;

-- Rows that failed a rule. Nothing is deleted without a trace.
CREATE TABLE IF NOT EXISTS silver.rejected_rows (
    reject_id   bigserial PRIMARY KEY,
    table_name  text        NOT NULL,
    row_key     text        NOT NULL,   -- business key, or file:row when the key itself is bad
    row_json    jsonb       NOT NULL,   -- the bronze row as received
    reason      text        NOT NULL,
    rejected_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (table_name, row_key, reason)
);

CREATE TABLE IF NOT EXISTS silver.restaurants (
    restaurant_id     text PRIMARY KEY,
    name              text NOT NULL,
    cuisine           text NOT NULL,            -- 'Unknown' when blank in the source
    cuisine_was_blank boolean NOT NULL,
    city              text NOT NULL,
    rating            numeric(3,1),
    onboarded_date    date,
    is_active         boolean,
    _source_file text, _batch_id bigint, _ingested_at timestamptz
);

CREATE TABLE IF NOT EXISTS silver.delivery_partners (
    partner_id  text PRIMARY KEY,
    name        text NOT NULL,
    phone       text,
    city        text NOT NULL,
    joined_date date,
    _source_file text, _batch_id bigint, _ingested_at timestamptz
);

CREATE TABLE IF NOT EXISTS silver.menu_items (
    item_id       text PRIMARY KEY,
    restaurant_id text NOT NULL,
    item_name     text NOT NULL,
    category      text,
    price         numeric(10,2) NOT NULL,
    _source_file text, _batch_id bigint, _ingested_at timestamptz
);

CREATE TABLE IF NOT EXISTS silver.customer_snapshots (
    customer_id     text NOT NULL,
    snapshot_date   date NOT NULL,
    name            text,
    phone           text,
    email           text,
    address_line    text,
    pincode         text,
    city            text NOT NULL,              -- 'Unknown' when missing in the source
    city_was_null   boolean NOT NULL,
    segment         text NOT NULL,
    signup_date     date,
    _source_file text, _batch_id bigint, _ingested_at timestamptz,
    PRIMARY KEY (customer_id, snapshot_date)
);

CREATE TABLE IF NOT EXISTS silver.orders (
    order_id            text PRIMARY KEY,
    customer_id         text NOT NULL,
    restaurant_id       text NOT NULL,
    delivery_partner_id text,
    city                text NOT NULL,
    city_was_null       boolean NOT NULL,
    status              text NOT NULL,
    platform            text,
    order_ts_utc        timestamptz NOT NULL,
    order_ts_ist        timestamp   NOT NULL,   -- wall-clock time in India
    order_date_ist      date        NOT NULL,   -- business date: always use this for "per day"
    delivered_ts_utc    timestamptz,
    delivery_minutes    integer,
    order_value         numeric(12,2) NOT NULL,
    discount            numeric(12,2) NOT NULL,
    delivery_fee        numeric(12,2) NOT NULL,
    taxes               numeric(12,2) NOT NULL,
    _source_file text, _batch_id bigint, _ingested_at timestamptz
);
CREATE INDEX IF NOT EXISTS silver_orders_date_idx ON silver.orders (order_date_ist);

CREATE TABLE IF NOT EXISTS silver.order_items (
    order_item_id text PRIMARY KEY,
    order_id      text NOT NULL,
    item_id       text,
    item_name     text,
    qty           integer NOT NULL,
    unit_price    numeric(10,2) NOT NULL,
    line_total    numeric(12,2) GENERATED ALWAYS AS (qty * unit_price) STORED,
    _source_file text, _batch_id bigint, _ingested_at timestamptz
);
CREATE INDEX IF NOT EXISTS silver_items_order_idx ON silver.order_items (order_id);

CREATE TABLE IF NOT EXISTS silver.payments (
    payment_id     text PRIMARY KEY,
    order_id       text NOT NULL,
    payment_ts_ist timestamp   NOT NULL,        -- the gateway sends IST with no marker
    payment_ts_utc timestamptz NOT NULL,
    method         text,
    status         text,
    amount         numeric(12,2) NOT NULL,
    _source_file text, _batch_id bigint, _ingested_at timestamptz
);
CREATE INDEX IF NOT EXISTS silver_payments_order_idx ON silver.payments (order_id);

CREATE TABLE IF NOT EXISTS silver.refunds (
    refund_id      text PRIMARY KEY,
    order_id       text NOT NULL,
    refund_ts_utc  timestamptz NOT NULL,
    refund_ts_ist  timestamp   NOT NULL,
    refund_amount  numeric(12,2) NOT NULL,
    reason_code    text,
    _source_file text, _batch_id bigint, _ingested_at timestamptz
);
CREATE INDEX IF NOT EXISTS silver_refunds_order_idx ON silver.refunds (order_id);

CREATE TABLE IF NOT EXISTS silver.ratings (
    order_id          text PRIMARY KEY,
    restaurant_rating integer NOT NULL CHECK (restaurant_rating BETWEEN 1 AND 5),
    delivery_rating   integer NOT NULL CHECK (delivery_rating BETWEEN 1 AND 5),
    rated_ts_utc      timestamptz NOT NULL,
    _source_file text, _batch_id bigint, _ingested_at timestamptz
);
