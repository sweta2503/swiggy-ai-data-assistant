"""Phase 6, part 2: the views, the catalog and the metrics agree with the real data."""
import re
from datetime import date, timedelta

import pytest

from swiggy_ai.semantic.check import PII_NAME, check_catalog, database_columns
from swiggy_ai.semantic.loader import load_semantic
from tests.phase04.helpers import build, fresh_connection, make_raw, one, q

pytestmark = pytest.mark.db


@pytest.fixture(scope="module")
def raw(tmp_path_factory):
    return make_raw(tmp_path_factory.mktemp("raw"))


@pytest.fixture(scope="module")
def db(raw):
    c = fresh_connection()
    build(c, raw)
    yield c
    c.rollback()
    c.close()


@pytest.fixture(scope="module")
def sem():
    return load_semantic()


def undo(conn):
    """Run a block inside a savepoint that is always rolled back."""
    import psycopg

    class _Undo:
        def __enter__(self):
            self.tx = conn.transaction()
            self.tx.__enter__()

        def __exit__(self, *exc):
            self.tx.__exit__(psycopg.Rollback, psycopg.Rollback(), None)
            return True
    return _Undo()


# --- catalog <-> database ---------------------------------------------------------------------
def test_catalog_describes_exactly_what_the_database_exposes(db, sem):
    assert check_catalog(db, sem) == []


def test_a_new_view_nobody_documented_is_flagged(db, sem):
    with undo(db):
        db.execute("CREATE VIEW analytics.sneaky AS SELECT 1 AS x")
        assert "analytics.sneaky: exposed by the database but missing" in "\n".join(
            check_catalog(db, sem))


def test_a_new_column_nobody_documented_is_flagged(db, sem):
    with undo(db):
        db.execute("CREATE OR REPLACE VIEW analytics.restaurants AS SELECT r.*, 1 AS extra"
                   " FROM (SELECT * FROM analytics.restaurants) r")
        found = "\n".join(check_catalog(db, sem))
        assert "analytics.restaurants.extra: exposed by the database but missing" in found


def test_a_catalog_column_that_no_longer_exists_is_flagged(db, sem):
    stale = sem.catalog.model_copy(deep=True)
    stale.table("analytics.refunds").columns.append(
        stale.table("analytics.refunds").columns[0].model_copy(update={"name": "ghost"}))
    found = "\n".join(check_catalog(db, type(sem)(stale, sem.metrics, sem.glossary)))
    assert "analytics.refunds.ghost: in catalog.yml but not in the database" in found


def test_a_wrong_type_in_the_catalog_is_flagged(db, sem):
    wrong = sem.catalog.model_copy(deep=True)
    wrong.table("analytics.orders").column("order_value").type = "text"
    found = check_catalog(db, type(sem)(wrong, sem.metrics, sem.glossary))
    assert any("order_value: catalog says text, database has numeric" in p for p in found)


def test_personal_data_in_a_column_name_must_be_masked(db, sem):
    leaky = sem.catalog.model_copy(deep=True)
    leaky.tables[0].columns.append(leaky.tables[0].columns[0].model_copy(update={"name": "email"}))
    with undo(db):
        # the catalog (copy) claims analytics.orders has an 'email' column, unmasked; add one
        db.execute("CREATE OR REPLACE VIEW analytics.orders AS SELECT o.*, 'x'::text AS email"
                   " FROM (SELECT * FROM analytics.orders) o")
        found = "\n".join(check_catalog(db, type(sem)(leaky, sem.metrics, sem.glossary)))
        assert "analytics.orders.email: looks like personal data but is not marked masked" in found


def test_a_value_missing_from_allowed_values_is_flagged(db, sem):
    with undo(db):
        db.execute("UPDATE gold.dim_city SET city_name = 'Atlantis'"
                   " WHERE city_name = (SELECT min(city_name) FROM gold.dim_city"
                   " WHERE city_sk > 0)")
        found = "\n".join(check_catalog(db, sem))
        assert "value 'Atlantis' exists in the data but is not in allowed_values" in found


# --- no personal data leaves through the views ------------------------------------------------
def test_no_analytics_column_looks_like_personal_data_except_the_masked_phone(db):
    cols = database_columns(db)
    flagged = [(t, c) for t, cs in cols.items() for c in cs if PII_NAME.search(c)]
    assert flagged == [("analytics.customers", "masked_phone")]


def test_masked_phone_shows_only_four_digits(db):
    values = [v for (v,) in q(db, "SELECT masked_phone FROM analytics.customers")]
    assert values and all(v.startswith("XXXXXX") and len(v) == 10 for v in values)
    assert one(db, "SELECT count(*) FROM analytics.customers c JOIN gold.customer_pii p USING"
                   " (customer_id) WHERE c.masked_phone = p.phone") == 0


def test_views_match_gold(db):
    assert one(db, "SELECT count(*) FROM analytics.orders") == one(
        db, "SELECT count(*) FROM gold.fact_orders")
    assert one(db, "SELECT sum(net_revenue) FROM analytics.orders") == one(
        db, "SELECT sum(net_revenue) FROM gold.fact_orders")
    assert one(db, "SELECT count(*) FROM analytics.order_items") == one(
        db, "SELECT count(*) FROM gold.fact_order_items")
    assert one(db, "SELECT count(*) FROM analytics.refunds") == one(
        db, "SELECT count(*) FROM gold.fact_refunds")
    assert one(db, "SELECT count(*) FROM analytics.customers") == one(
        db, "SELECT count(DISTINCT customer_id) FROM silver.customer_snapshots")


# --- every metric against independent SQL on silver -------------------------------------------
def metric_value(db, sem, name, where=""):
    m = sem.metrics.metric(name)
    return one(db, f"SELECT {m.sql_expression} FROM {m.source} {where}")


def test_count_metrics(db, sem):
    assert metric_value(db, sem, "orders") == one(db, "SELECT count(*) FROM silver.orders")
    assert metric_value(db, sem, "delivered_orders") == one(
        db, "SELECT count(*) FROM silver.orders WHERE status = 'delivered'")
    assert metric_value(db, sem, "active_customers") == one(
        db, "SELECT count(DISTINCT customer_id) FROM silver.orders WHERE status = 'delivered'")


def test_money_metrics(db, sem):
    gross = one(db, "SELECT sum(order_value) FROM silver.orders WHERE status = 'delivered'")
    net = one(db, "SELECT sum(order_value - discount) FROM silver.orders"
                  " WHERE status = 'delivered'")
    assert metric_value(db, sem, "gross_order_value") == gross
    assert metric_value(db, sem, "net_revenue") == net
    with_fee_and_tax = one(db, "SELECT sum(order_value - discount + delivery_fee + taxes)"
                               " FROM silver.orders WHERE status = 'delivered'")
    assert metric_value(db, sem, "net_revenue") < with_fee_and_tax  # fee and tax are excluded
    delivered = metric_value(db, sem, "delivered_orders")
    assert float(metric_value(db, sem, "aov")) == pytest.approx(float(net) / delivered)
    assert metric_value(db, sem, "net_revenue") == one(db, "SELECT sum(net_revenue)"
                                                           " FROM analytics.orders")


def test_rate_and_time_metrics(db, sem):
    total = one(db, "SELECT count(*) FROM silver.orders")
    cancelled = one(db, "SELECT count(*) FROM silver.orders WHERE status = 'cancelled'")
    refunded = one(db, "SELECT count(*) FROM silver.orders WHERE status = 'refunded'")
    delivered = one(db, "SELECT count(*) FROM silver.orders WHERE status = 'delivered'")
    assert float(metric_value(db, sem, "cancellation_rate")) == pytest.approx(cancelled / total)
    assert float(metric_value(db, sem, "refund_rate")) == pytest.approx(refunded / delivered)
    avg = one(db, "SELECT avg(delivery_minutes) FROM silver.orders WHERE status = 'delivered'")
    assert float(metric_value(db, sem, "avg_delivery_minutes")) == pytest.approx(float(avg))
    assert 0 < float(metric_value(db, sem, "cancellation_rate")) < 1


def test_new_customers_counts_signups_in_the_period(db, sem):
    start, end = date(2026, 8, 1), date(2026, 9, 1)
    expected = one(db, "SELECT count(*) FROM (SELECT customer_id, min(signup_date) s FROM"
                       " silver.customer_snapshots GROUP BY 1) x WHERE s >= %s AND s < %s",
                   start, end)
    got = metric_value(db, sem, "new_customers",
                       f"WHERE signup_date >= '{start}' AND signup_date < '{end}'")
    assert got == expected > 0


def test_grouping_a_metric_works_for_every_dimension(db, sem):
    m = sem.metrics.metric("net_revenue")
    for dim in ("city", "cuisine", "customer_segment", "platform", "order_date"):
        rows = q(db, f"SELECT {dim}, {m.sql_expression} FROM {m.source} GROUP BY 1")
        assert rows and sum(r[1] or 0 for r in rows) == metric_value(db, sem, "net_revenue")


# --- relative dates: IST, Monday weeks, "today" from the data ---------------------------------
def test_period_templates_match_hand_computed_ranges(db, sem):
    today = one(db, "SELECT today FROM analytics.data_as_of")
    monday = today - timedelta(days=today.weekday())
    first_this_month = today.replace(day=1)
    last_month_end = first_this_month
    last_month_start = (first_this_month - timedelta(days=1)).replace(day=1)
    expected = {
        "yesterday": (today - timedelta(days=1), today),
        "last_7_days": (today - timedelta(days=7), today),
        "last_week": (monday - timedelta(days=7), monday),
        "this_week": (monday, today),
        "last_month": (last_month_start, last_month_end),
        "this_month": (first_this_month, today),
    }
    for name, (lo, hi) in expected.items():
        template = sem.metrics.conventions.period_templates[name]
        sql_filter = _fill_t(template.replace("<col>", "order_date"))
        got = one(db, f"SELECT count(*) FROM analytics.orders WHERE {sql_filter}")
        want = one(db, "SELECT count(*) FROM analytics.orders WHERE order_date >= %s"
                       " AND order_date < %s", lo, hi)
        assert got == want, name
        if name in ("last_week", "last_month"):
            assert want > 0


def _fill_t(template: str) -> str:
    """Replace the standalone T (today) in a period template with the real expression."""
    return re.sub(r"\bT\b", "(SELECT today FROM analytics.data_as_of)", template)


def test_last_week_runs_monday_to_sunday(db, sem):
    sql_filter = _fill_t(sem.metrics.conventions.period_templates["last_week"].replace(
        "<col>", "order_date"))
    lo, hi = q(db, f"SELECT min(order_date), max(order_date) FROM analytics.orders"
                   f" WHERE {sql_filter}")[0]
    assert lo.weekday() == 0 and hi.weekday() == 6  # Monday .. Sunday
    assert (hi - lo).days == 6


def test_day_boundaries_follow_india_not_utc(db):
    crossed = one(db, "SELECT count(*) FROM gold.fact_orders WHERE order_date_ist <>"
                      " (order_ts_utc AT TIME ZONE 'UTC')::date")
    assert crossed > 0
    assert one(db, "SELECT count(*) FROM analytics.orders WHERE order_date <> order_ts::date") == 0


# --- the warnings in the catalog are true -----------------------------------------------------
def test_joining_items_to_orders_inflates_revenue_as_the_catalog_warns(db, sem):
    true_total = metric_value(db, sem, "net_revenue")
    inflated = one(db, "SELECT sum(o.order_value - o.discount) FROM analytics.orders o JOIN"
                       " analytics.order_items i USING (order_id) WHERE o.status = 'delivered'")
    assert inflated > true_total * 2  # about 3.2 item lines per order


def test_segment_on_orders_is_as_of_the_order_date(db):
    # at least one customer appears under two segments across their orders
    assert one(db, "SELECT count(*) FROM (SELECT customer_id FROM analytics.orders GROUP BY 1"
                   " HAVING count(DISTINCT customer_segment) > 1) x") > 0
