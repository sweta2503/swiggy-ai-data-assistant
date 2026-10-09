"""Phase 4: the star schema is complete, correct, and keeps history.

Read-only tests share one loaded connection (loaded once). Tests that change data use their own
connection. Everything is rolled back at the end, so real gold data is never touched.
"""
import json
from pathlib import Path

import psycopg
import pytest

from tests.phase04.helpers import build, fresh_connection, make_raw, one, q

pytestmark = pytest.mark.db


@pytest.fixture(scope="module")
def raw(tmp_path_factory) -> Path:
    out = tmp_path_factory.mktemp("raw")
    return make_raw(out)


@pytest.fixture(scope="module")
def defects(raw) -> dict:
    return json.loads((raw / "_defects.json").read_text())


@pytest.fixture(scope="module")
def meta(raw) -> dict:
    return json.loads((raw / "_meta.json").read_text())


@pytest.fixture(scope="module")
def db(raw):
    c = fresh_connection()
    build(c, raw)
    yield c
    c.rollback()
    c.close()


# --- completeness -----------------------------------------------------------------------------
def test_every_order_appears_exactly_once(db, meta, defects):
    n = one(db, "SELECT count(*) FROM gold.fact_orders")
    assert n == one(db, "SELECT count(DISTINCT order_id) FROM gold.fact_orders")
    assert n == meta["unique_orders"] - defects["delivery_minutes_outliers"]["rows"]
    assert one(db, "SELECT count(*) FROM gold.fact_orders f FULL JOIN silver.orders s"
                   " USING (order_id) WHERE f.order_id IS NULL OR s.order_id IS NULL") == 0


def test_child_facts_match_silver(db):
    assert one(db, "SELECT count(*) FROM gold.fact_order_items") == one(
        db, "SELECT count(*) FROM silver.order_items")
    assert one(db, "SELECT sum(line_total) FROM gold.fact_order_items") == one(
        db, "SELECT sum(line_total) FROM silver.order_items")
    assert one(db, "SELECT count(*) FROM gold.fact_refunds") == one(
        db, "SELECT count(*) FROM silver.refunds")
    assert one(db, "SELECT sum(refund_amount) FROM gold.fact_refunds") == one(
        db, "SELECT sum(refund_amount) FROM silver.refunds")


def test_revenue_reconciles_to_silver_and_to_the_aggregate(db):
    gold = one(db, "SELECT sum(net_revenue) FROM gold.fact_orders")
    silver = one(db, "SELECT sum(order_value - discount) FROM silver.orders"
                     " WHERE status = 'delivered'")
    assert gold == silver > 0
    assert one(db, "SELECT sum(net_revenue) FROM gold.daily_city_sales") == gold
    # per day, too
    assert one(db, "SELECT count(*) FROM (SELECT order_date_ist, sum(net_revenue) a"
                   " FROM gold.fact_orders GROUP BY 1) f JOIN (SELECT order_date_ist,"
                   " sum(net_revenue) b FROM gold.daily_city_sales GROUP BY 1) d USING"
                   " (order_date_ist) WHERE f.a <> d.b") == 0


def test_net_revenue_follows_the_business_definition(db):
    assert one(db, "SELECT count(*) FROM gold.fact_orders WHERE status <> 'delivered'"
                   " AND net_revenue <> 0") == 0
    assert one(db, "SELECT count(*) FROM gold.fact_orders WHERE status = 'delivered'"
                   " AND net_revenue <> order_value - discount") == 0


def test_aggregate_counts_add_up(db):
    assert one(db, "SELECT count(*) FROM gold.daily_city_sales WHERE orders <> delivered_orders"
                   " + cancelled_orders + refunded_orders + failed_orders") == 0
    assert one(db, "SELECT sum(orders) FROM gold.daily_city_sales") == one(
        db, "SELECT count(*) FROM gold.fact_orders")


# --- SCD Type 2 -------------------------------------------------------------------------------
def test_scd2_windows_never_overlap_and_one_row_is_current(db):
    overlaps = one(db, "SELECT count(*) FROM gold.dim_customer a JOIN gold.dim_customer b"
                       " ON a.customer_id = b.customer_id AND a.customer_sk < b.customer_sk"
                       " AND a.valid_from < b.valid_to AND b.valid_from < a.valid_to")
    assert overlaps == 0
    assert one(db, "SELECT count(*) FROM (SELECT customer_id FROM gold.dim_customer GROUP BY 1"
                   " HAVING count(*) FILTER (WHERE is_current) <> 1) x") == 0
    # versions are back to back: each one starts where the previous ended
    assert one(db, "SELECT count(*) FROM (SELECT valid_from, lag(valid_to) OVER (PARTITION BY"
                   " customer_id ORDER BY valid_from) prev_to FROM gold.dim_customer) x"
                   " WHERE prev_to IS NOT NULL AND prev_to <> valid_from") == 0


def test_scd2_has_a_version_for_every_recorded_change(db, defects):
    scd2 = defects["scd2_changes"]
    changed = set(scd2["city_change_customer_ids"]) | set(scd2["segment_change_customer_ids"])
    multi = {c for (c,) in q(db, "SELECT customer_id FROM gold.dim_customer GROUP BY 1"
                                 " HAVING count(*) > 1")}
    assert multi == changed and changed
    # a city change really changes the city, and the new version starts on the snapshot date
    for cid in scd2["city_change_customer_ids"][:10]:
        v = q(db, "SELECT city, valid_from, valid_to FROM gold.dim_customer WHERE customer_id"
                  " = %s ORDER BY valid_from", cid)
        assert v[0][0] != v[-1][0]
        assert v[0][2] == v[1][1]
        assert one(db, "SELECT count(*) FROM silver.customer_snapshots WHERE customer_id = %s"
                       " AND snapshot_date = %s AND city = %s", cid, v[1][1], v[1][0]) == 1


def test_orders_point_at_the_customer_version_valid_on_the_day(db):
    wrong = one(db, "SELECT count(*) FROM gold.fact_orders f JOIN gold.dim_customer c USING"
                    " (customer_sk) WHERE c.customer_sk <> -1 AND NOT"
                    " (f.order_date_ist >= c.valid_from AND f.order_date_ist < c.valid_to)")
    assert wrong == 0
    # for customers who moved, orders before and after the move use different versions
    both = one(db, "SELECT count(*) FROM (SELECT c.customer_id FROM gold.fact_orders f JOIN"
                   " gold.dim_customer c USING (customer_sk) GROUP BY 1 HAVING count(DISTINCT"
                   " c.customer_sk) > 1) x")
    assert both > 0


# --- PII separation ---------------------------------------------------------------------------
def test_contact_details_live_only_in_customer_pii(db):
    cols = q(db, "SELECT table_name, column_name FROM information_schema.columns"
                 " WHERE table_schema = 'gold' AND column_name IN ('phone', 'email',"
                 " 'address_line', 'pincode', 'customer_name')")
    assert {t for t, _ in cols} == {"customer_pii"}
    assert one(db, "SELECT count(*) FROM gold.customer_pii") == one(
        db, "SELECT count(DISTINCT customer_id) FROM silver.customer_snapshots")
    assert one(db, "SELECT count(*) FROM gold.customer_pii WHERE phone !~ '^[6-9][0-9]{9}$'") == 0


# --- keys and constraints ---------------------------------------------------------------------
def test_unknown_member_catches_missing_dimension_rows(db):
    no_partner = one(db, "SELECT count(*) FROM silver.orders WHERE delivery_partner_id IS NULL")
    assert no_partner > 0
    assert one(db, "SELECT count(*) FROM gold.fact_orders WHERE partner_sk = -1") == no_partner
    assert one(db, "SELECT count(*) FROM gold.fact_orders WHERE customer_sk = -1"
                   " OR restaurant_sk = -1 OR city_sk = -1") == 0


def test_foreign_keys_are_enforced(db):
    bad = ("INSERT INTO gold.fact_orders (order_id, order_date_ist, order_ts_utc, order_ts_ist,"
           " customer_sk, restaurant_sk, partner_sk, city_sk, status, order_value, discount,"
           " delivery_fee, taxes, net_revenue, _silver_ingested_at)"
           " SELECT 'X', min(order_date_ist), now(), now(), -1, 999999, -1, -1, 'delivered',"
           " 1, 0, 0, 0, 1, now()"
           " FROM gold.fact_orders")
    with pytest.raises(psycopg.errors.ForeignKeyViolation), db.transaction():
        db.execute(bad)
    with pytest.raises(psycopg.errors.ForeignKeyViolation), db.transaction():
        db.execute("INSERT INTO gold.fact_order_items (order_item_id, order_id, order_date_ist,"
                   " restaurant_sk, qty, unit_price, line_total, _silver_ingested_at)"
                   " VALUES ('X', 'NO-SUCH-ORDER', DATE '2026-09-01', -1, 1, 1, 1, now())")


# --- partitioning -----------------------------------------------------------------------------
def scanned_partitions(conn, sql) -> set[str]:
    plan = conn.execute("EXPLAIN (FORMAT JSON) " + sql).fetchone()[0]  # psycopg decodes JSON
    found: set[str] = set()

    def walk(node):
        if isinstance(node, dict):
            name = node.get("Relation Name")
            if name and name.startswith("fact_orders_"):
                found.add(name)
            for v in node.values():
                walk(v)
        elif isinstance(node, list):
            for v in node:
                walk(v)
    walk(plan)
    return found


def test_one_month_query_reads_one_partition(db):
    one_month = ("SELECT count(*) FROM gold.fact_orders WHERE order_date_ist >= DATE"
                 " '2026-09-01' AND order_date_ist < DATE '2026-10-01'")
    assert scanned_partitions(db, one_month) == {"fact_orders_2026_09"}
    two_months = one_month.replace("'2026-09-01'", "'2026-08-01'")
    assert scanned_partitions(db, two_months) == {"fact_orders_2026_08", "fact_orders_2026_09"}
    assert one(db, "SELECT count(*) FROM gold.fact_orders_default") == 0  # nothing strays


# --- calendar ---------------------------------------------------------------------------------
def test_dim_date_is_complete_and_correct(db, meta):
    assert one(db, "SELECT count(*) FROM gold.fact_orders f LEFT JOIN gold.dim_date d ON"
                   " d.full_date = f.order_date_ist WHERE d.full_date IS NULL") == 0
    assert one(db, "SELECT count(*) FROM gold.dim_date WHERE is_weekend <>"
                   " (extract(isodow FROM full_date) >= 6)") == 0
    assert one(db, "SELECT count(*) FROM gold.dim_date WHERE extract(isodow FROM week_start_date)"
                   " <> 1") == 0
    festivals = {d.isoformat() for (d,) in q(
        db, "SELECT full_date FROM gold.dim_date WHERE is_festival")}
    assert festivals == set(meta["festival_dates"]) and len(festivals) == 2


# --- the story is discoverable in gold --------------------------------------------------------
def test_mumbai_refund_spike_is_discoverable(db, meta):
    spike = meta["refund_spike"]
    top = q(db, "SELECT reason_code, count(*) FROM gold.fact_refunds r JOIN gold.dim_city c USING"
                " (city_sk) JOIN gold.dim_date d ON d.full_date = r.refund_date_ist WHERE"
                " c.city_name = 'Mumbai' AND to_char(d.month_start_date, 'YYYY-MM') = %s"
                " GROUP BY 1 ORDER BY 2 DESC", spike["month"])
    assert top[0][0] == spike["reason_code"]
    cluster = set(spike["restaurant_ids"])
    in_cluster = one(db, "SELECT count(*) FROM gold.fact_refunds r JOIN gold.dim_restaurant x"
                         " USING (restaurant_sk) WHERE r.reason_code = %s"
                         " AND x.restaurant_id = ANY(%s)", spike["reason_code"], list(cluster))
    assert in_cluster / top[0][1] > 0.8
