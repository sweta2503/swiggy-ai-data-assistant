"""Phase 5, part 2: the full suite on a real pipeline build, and one test per way data goes bad.

Each corruption is applied inside a savepoint and undone, so tests do not affect each other.
"""
from contextlib import contextmanager
from pathlib import Path

import psycopg
import pytest

from swiggy_ai.dq.checks import build_checks
from swiggy_ai.dq.runner import assert_gold_healthy, default_as_of, run_checks
from tests.phase04.helpers import build, fresh_connection, make_raw, one

pytestmark = pytest.mark.db


@pytest.fixture(scope="module")
def raw(tmp_path_factory) -> Path:
    return make_raw(tmp_path_factory.mktemp("raw"))


@pytest.fixture(scope="module")
def db(raw):
    c = fresh_connection()
    build(c, raw)
    yield c
    c.rollback()
    c.close()


@contextmanager
def corrupted(conn, statement: str, *params):
    """Apply one bad change, yield, then undo it."""
    with _savepoint(conn):
        conn.execute(statement, params or None)
        yield


@contextmanager
def _savepoint(conn):
    try:
        with conn.transaction():
            yield
            raise psycopg.Rollback()
    except psycopg.Rollback:
        pass


def failing(conn, raw, **kw) -> dict[str, str]:
    report = run_checks(conn, as_of=kw.get("as_of", default_as_of(raw)), store=False)
    return {r.check.name: r.check.severity for r in report.failures}


def test_a_clean_build_passes_every_check(db, raw):
    report = run_checks(db, as_of=default_as_of(raw))
    assert [r.check.name for r in report.failures] == []
    assert len(report.results) == len(build_checks())
    assert report.ok and assert_gold_healthy(db)


def test_values_out_of_range_are_caught(db, raw):
    with corrupted(db, "UPDATE gold.fact_orders SET delivery_minutes = 999"
                       " WHERE order_id = (SELECT min(order_id) FROM gold.fact_orders"
                       " WHERE delivery_minutes IS NOT NULL)"):
        assert "range:gold.fact_orders.delivery_minutes" in failing(db, raw)


def test_unexpected_status_is_caught(db, raw):
    with corrupted(db, "UPDATE gold.fact_orders SET status = 'teleported'"
                       " WHERE order_id = (SELECT min(order_id) FROM gold.fact_orders)"):
        assert "accepted_values:gold.fact_orders.status" in failing(db, raw)


def test_discount_above_value_is_caught(db, raw):
    with corrupted(db, "UPDATE gold.fact_orders SET discount = order_value + 10"
                       " WHERE order_id = (SELECT min(order_id) FROM gold.fact_orders)"):
        assert "rule:discount_not_above_value" in failing(db, raw)


def test_orders_pointing_at_unknown_customers_are_caught(db, raw):
    with corrupted(db, "UPDATE gold.fact_orders SET customer_sk = -1"
                       " WHERE order_id = (SELECT min(order_id) FROM gold.fact_orders)"):
        assert "unknown_member:gold.fact_orders" in failing(db, raw)


def test_stale_data_fails_freshness(db, raw):
    from datetime import timedelta
    late = default_as_of(raw) + timedelta(days=3)
    f = failing(db, raw, as_of=late)
    assert f.get("freshness:gold.fact_orders.order_ts_utc") == "blocking"


def test_gold_drifting_from_silver_is_caught(db, raw):
    # silver loses an order that gold still has
    with corrupted(db, "DELETE FROM silver.orders WHERE order_id = (SELECT min(order_id)"
                       " FROM silver.orders WHERE status = 'delivered')"):
        f = failing(db, raw)
        assert f.get("reconciliation:order_count_by_day") == "blocking"
        assert f.get("reconciliation:net_revenue_by_day") == "blocking"
        assert "relationships:silver.order_items.order_id->silver.orders.order_id" in f


def test_aggregate_drifting_from_the_fact_is_caught(db, raw):
    with corrupted(db, "UPDATE gold.daily_city_sales SET net_revenue = net_revenue + 100"
                       " WHERE (order_date_ist, city_sk) = (SELECT order_date_ist, city_sk"
                       " FROM gold.daily_city_sales LIMIT 1)"):
        assert "reconciliation:daily_city_sales" in failing(db, raw)


def test_rejected_rows_going_missing_is_caught(db, raw):
    # an order disappears from silver AND from the reject table: it was silently lost
    with corrupted(db, "DELETE FROM silver.orders WHERE order_id = (SELECT min(order_id)"
                       " FROM silver.orders)"):
        assert "row_accounting:bronze.orders->silver" in failing(db, raw)


def test_overlapping_customer_versions_are_caught(db, raw):
    with corrupted(db, "INSERT INTO gold.dim_customer (customer_id, city_sk, city, segment,"
                       " valid_from, valid_to, is_current) SELECT customer_id, city_sk, city,"
                       " segment, valid_from + 1, valid_from + 5, false FROM gold.dim_customer"
                       " WHERE is_current AND customer_id <> 'UNKNOWN' LIMIT 1"):
        assert failing(db, raw).get("scd2_integrity:overlapping_windows") == "blocking"


def test_a_gap_between_versions_is_caught(db, raw):
    with corrupted(db, "UPDATE gold.dim_customer SET valid_to = valid_to - 1 WHERE customer_id ="
                       " (SELECT customer_id FROM gold.dim_customer GROUP BY 1 HAVING count(*)"
                       " > 1 LIMIT 1) AND NOT is_current"):
        assert failing(db, raw).get("scd2_integrity:gaps") == "blocking"


def test_two_current_versions_are_caught(db, raw):
    with corrupted(db, "UPDATE gold.dim_customer SET is_current = false WHERE customer_id ="
                       " (SELECT customer_id FROM gold.dim_customer WHERE is_current AND"
                       " customer_id <> 'UNKNOWN' LIMIT 1)"):
        f = failing(db, raw)
        assert f.get("scd2_integrity:one_current_row") == "blocking"
        assert f.get("scd2_integrity:current_matches_open_window") == "blocking"


def test_warnings_do_not_make_gold_unhealthy(db, raw):
    with corrupted(db, "UPDATE gold.fact_refunds SET reason_code = 'ALIENS'"
                       " WHERE refund_id = (SELECT min(refund_id) FROM gold.fact_refunds)"):
        report = run_checks(db, as_of=default_as_of(raw))
        assert [r.check.name for r in report.failures] == [
            "accepted_values:gold.fact_refunds.reason_code"]
        assert report.ok and assert_gold_healthy(db)  # a warning, not a blocker


def test_results_table_records_the_whole_run(db, raw):
    report = run_checks(db, as_of=default_as_of(raw))
    n = one(db, "SELECT count(*) FROM ops.dq_results WHERE run_id = %s", report.run_id)
    assert n == len(build_checks())
    assert one(db, "SELECT count(DISTINCT layer) FROM ops.dq_results WHERE run_id = %s",
               report.run_id) == 2
