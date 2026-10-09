-- Bronze tables: one per source file type. Every source column is text (nothing can fail
-- on load); casting and cleaning happen in silver. The last five columns are load metadata.

CREATE TABLE IF NOT EXISTS bronze.orders (
    order_id             text,
    customer_id          text,
    restaurant_id        text,
    delivery_partner_id  text,
    city                 text,
    status               text,
    platform             text,
    order_ts             text,
    delivered_ts         text,
    delivery_minutes     text,
    order_value          text,
    discount             text,
    delivery_fee         text,
    taxes                text,
    _source_file         text        NOT NULL,
    _file_hash           text        NOT NULL,
    _ingested_at         timestamptz NOT NULL,
    _batch_id            bigint      NOT NULL,
    _row_number          integer     NOT NULL
);
CREATE INDEX IF NOT EXISTS orders_batch_idx ON bronze.orders (_batch_id);

CREATE TABLE IF NOT EXISTS bronze.payments (
    payment_id           text,
    order_id             text,
    payment_ts           text,
    method               text,
    status               text,
    amount               text,
    _source_file         text        NOT NULL,
    _file_hash           text        NOT NULL,
    _ingested_at         timestamptz NOT NULL,
    _batch_id            bigint      NOT NULL,
    _row_number          integer     NOT NULL
);
CREATE INDEX IF NOT EXISTS payments_batch_idx ON bronze.payments (_batch_id);

CREATE TABLE IF NOT EXISTS bronze.customer_snapshots (
    snapshot_date        text,
    customer_id          text,
    name                 text,
    phone                text,
    email                text,
    address_line         text,
    pincode              text,
    city                 text,
    segment              text,
    signup_date          text,
    _source_file         text        NOT NULL,
    _file_hash           text        NOT NULL,
    _ingested_at         timestamptz NOT NULL,
    _batch_id            bigint      NOT NULL,
    _row_number          integer     NOT NULL
);
CREATE INDEX IF NOT EXISTS customer_snapshots_batch_idx ON bronze.customer_snapshots (_batch_id);

CREATE TABLE IF NOT EXISTS bronze.order_items (
    order_item_id        text,
    order_id             text,
    item_id              text,
    item_name            text,
    qty                  text,
    unit_price           text,
    _source_file         text        NOT NULL,
    _file_hash           text        NOT NULL,
    _ingested_at         timestamptz NOT NULL,
    _batch_id            bigint      NOT NULL,
    _row_number          integer     NOT NULL
);
CREATE INDEX IF NOT EXISTS order_items_batch_idx ON bronze.order_items (_batch_id);

CREATE TABLE IF NOT EXISTS bronze.refunds (
    refund_id            text,
    order_id             text,
    refund_ts            text,
    refund_amount        text,
    reason_code          text,
    _source_file         text        NOT NULL,
    _file_hash           text        NOT NULL,
    _ingested_at         timestamptz NOT NULL,
    _batch_id            bigint      NOT NULL,
    _row_number          integer     NOT NULL
);
CREATE INDEX IF NOT EXISTS refunds_batch_idx ON bronze.refunds (_batch_id);

CREATE TABLE IF NOT EXISTS bronze.ratings (
    order_id             text,
    restaurant_rating    text,
    delivery_rating      text,
    rated_ts             text,
    _source_file         text        NOT NULL,
    _file_hash           text        NOT NULL,
    _ingested_at         timestamptz NOT NULL,
    _batch_id            bigint      NOT NULL,
    _row_number          integer     NOT NULL
);
CREATE INDEX IF NOT EXISTS ratings_batch_idx ON bronze.ratings (_batch_id);

CREATE TABLE IF NOT EXISTS bronze.restaurants (
    restaurant_id        text,
    name                 text,
    cuisine              text,
    city                 text,
    rating               text,
    onboarded_date       text,
    is_active            text,
    _source_file         text        NOT NULL,
    _file_hash           text        NOT NULL,
    _ingested_at         timestamptz NOT NULL,
    _batch_id            bigint      NOT NULL,
    _row_number          integer     NOT NULL
);
CREATE INDEX IF NOT EXISTS restaurants_batch_idx ON bronze.restaurants (_batch_id);

CREATE TABLE IF NOT EXISTS bronze.delivery_partners (
    partner_id           text,
    name                 text,
    phone                text,
    city                 text,
    joined_date          text,
    _source_file         text        NOT NULL,
    _file_hash           text        NOT NULL,
    _ingested_at         timestamptz NOT NULL,
    _batch_id            bigint      NOT NULL,
    _row_number          integer     NOT NULL
);
CREATE INDEX IF NOT EXISTS delivery_partners_batch_idx ON bronze.delivery_partners (_batch_id);

CREATE TABLE IF NOT EXISTS bronze.menu_items (
    item_id              text,
    restaurant_id        text,
    item_name            text,
    category             text,
    price                text,
    _source_file         text        NOT NULL,
    _file_hash           text        NOT NULL,
    _ingested_at         timestamptz NOT NULL,
    _batch_id            bigint      NOT NULL,
    _row_number          integer     NOT NULL
);
CREATE INDEX IF NOT EXISTS menu_items_batch_idx ON bronze.menu_items (_batch_id);
