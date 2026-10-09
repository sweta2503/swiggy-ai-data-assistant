-- SCD Type 2 customer dimension, built from the customer snapshots.
--   1. Keep a snapshot only if it is a customer's first, or city/segment changed since the last.
--   2. Each kept snapshot opens a version; it closes when the next one opens.
--   3. Upsert by (customer_id, valid_from) so existing surrogate keys never change.
-- Order of steps matters: close old versions BEFORE inserting new ones, because the database
-- allows only one current row per customer.

DROP TABLE IF EXISTS _cust_versions;
CREATE TEMP TABLE _cust_versions AS
WITH snaps AS (
    SELECT customer_id, snapshot_date, city, segment, signup_date,
           lag(city)    OVER w AS prev_city,
           lag(segment) OVER w AS prev_segment
    FROM silver.customer_snapshots
    WINDOW w AS (PARTITION BY customer_id ORDER BY snapshot_date)
), changes AS (
    SELECT * FROM snaps
    WHERE prev_city IS NULL OR city <> prev_city OR segment <> prev_segment
), versions AS (
    SELECT customer_id, city, segment,
           -- the first version is valid since signup (the earliest date we can vouch for)
           CASE WHEN row_number() OVER w = 1
                THEN least(coalesce(signup_date, snapshot_date), snapshot_date)
                ELSE snapshot_date END AS valid_from,
           lead(snapshot_date) OVER w AS next_from
    FROM changes
    WINDOW w AS (PARTITION BY customer_id ORDER BY snapshot_date)
)
SELECT v.customer_id, c.city_sk, v.city, v.segment, v.valid_from,
       coalesce(v.next_from, DATE '9999-12-31') AS valid_to,
       v.next_from IS NULL AS is_current
FROM versions v JOIN gold.dim_city c ON c.city_name = v.city;

-- close / correct versions that already exist
UPDATE gold.dim_customer d
SET city_sk = v.city_sk, city = v.city, segment = v.segment,
    valid_to = v.valid_to, is_current = v.is_current
FROM _cust_versions v
WHERE d.customer_id = v.customer_id AND d.valid_from = v.valid_from
  AND (d.valid_to, d.is_current, d.city, d.segment)
      IS DISTINCT FROM (v.valid_to, v.is_current, v.city, v.segment);

-- open new versions
INSERT INTO gold.dim_customer (customer_id, city_sk, city, segment, valid_from, valid_to,
                               is_current)
SELECT v.customer_id, v.city_sk, v.city, v.segment, v.valid_from, v.valid_to, v.is_current
FROM _cust_versions v
WHERE NOT EXISTS (SELECT 1 FROM gold.dim_customer d
                  WHERE d.customer_id = v.customer_id AND d.valid_from = v.valid_from);

-- contact details: latest snapshot per customer, kept apart from the dimension
INSERT INTO gold.customer_pii AS t (customer_id, name, phone, email, address_line, pincode)
SELECT DISTINCT ON (customer_id) customer_id, name, phone, email, address_line, pincode
FROM silver.customer_snapshots
ORDER BY customer_id, snapshot_date DESC
ON CONFLICT (customer_id) DO UPDATE SET
    name = EXCLUDED.name, phone = EXCLUDED.phone, email = EXCLUDED.email,
    address_line = EXCLUDED.address_line, pincode = EXCLUDED.pincode, updated_at = now()
WHERE (t.name, t.phone, t.email, t.address_line, t.pincode)
      IS DISTINCT FROM (EXCLUDED.name, EXCLUDED.phone, EXCLUDED.email, EXCLUDED.address_line,
                        EXCLUDED.pincode);
