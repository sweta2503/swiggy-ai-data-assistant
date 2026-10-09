"""Phase 5, part 1: every kind of check can pass AND fail, and results are stored and summarised.

These tests use throwaway temp tables, so they need no pipeline data.
"""
import uuid
from datetime import UTC, datetime, timedelta

import pytest

from swiggy_ai.db import connect
from swiggy_ai.dq import checks as ck
from swiggy_ai.dq.runner import assert_gold_healthy, run_checks

pytestmark = pytest.mark.db
NOW = datetime(2026, 9, 30, 12, 0, tzinfo=UTC)


@pytest.fixture
def conn():
    c = connect("etl")
    c.execute("CREATE TEMP TABLE orders (id int, status text, minutes int, d date, ts timestamptz)")
    c.execute("CREATE TEMP TABLE items (order_id int)")
    c.execute("CREATE TEMP TABLE skipped (id int)")
    yield c
    c.rollback()
    c.close()


def run(conn, check, as_of=NOW) -> ck.Outcome:
    return check.run(conn, ck.Context(as_of=as_of))


def test_not_null(conn):
    conn.execute("INSERT INTO orders (id, status) VALUES (1, 'a'), (NULL, 'b'), (NULL, 'c')")
    out = run(conn, ck.not_null("pg_temp", "orders", "id", layer="silver"))
    assert (out.observed, out.passed) == (2, False)
    assert run(conn, ck.not_null("pg_temp", "orders", "status", layer="silver")).passed


def test_unique_counts_the_extra_copies(conn):
    conn.execute("INSERT INTO orders (id) VALUES (1), (1), (1), (2), (3), (3)")
    out = run(conn, ck.unique("pg_temp", "orders", ["id"], layer="gold"))
    assert (out.observed, out.passed) == (3, False)  # two extra of id 1, one extra of id 3
    conn.execute("DELETE FROM orders")
    conn.execute("INSERT INTO orders (id, status) VALUES (1, 'a'), (1, 'b')")
    assert run(conn, ck.unique("pg_temp", "orders", ["id", "status"], layer="gold")).passed


def test_accepted_values(conn):
    conn.execute("INSERT INTO orders (status) VALUES ('ok'), ('ok'), ('weird'), (NULL)")
    out = run(conn, ck.accepted_values("pg_temp", "orders", "status", ["ok"], layer="gold"))
    assert (out.observed, out.passed) == (1, False)  # NULL is not_null's job, not this check's


def test_relationships_with_a_documented_exception(conn):
    conn.execute("INSERT INTO orders (id) VALUES (1), (2)")
    conn.execute("INSERT INTO items VALUES (1), (2), (3), (4)")
    child, parent = ("pg_temp", "items", "order_id"), ("pg_temp", "orders", "id")
    out = run(conn, ck.relationships(child, parent, layer="silver"))
    assert (out.observed, out.passed) == (2, False)  # items 3 and 4 have no order
    excused = ck.relationships(child, parent, layer="silver", ignore_if="c.order_id = 3")
    out = run(conn, excused)
    assert (out.observed, out.passed) == (1, False)  # only item 4 is unexplained


def test_value_range_ignores_nulls(conn):
    conn.execute("INSERT INTO orders (minutes) VALUES (30), (NULL), (-5), (999), (240)")
    out = run(conn, ck.value_range("pg_temp", "orders", "minutes", 1, 240, layer="gold"))
    assert (out.observed, out.passed) == (2, False)


def test_freshness_measures_hours_against_as_of(conn):
    conn.execute("INSERT INTO orders (ts) VALUES (%s)", (NOW - timedelta(hours=10),))
    check = ck.freshness("pg_temp", "orders", "ts", 24, layer="gold")
    assert run(conn, check).passed
    late = run(conn, check, as_of=NOW + timedelta(hours=30))
    assert (late.observed, late.passed) == (40, False)
    conn.execute("DELETE FROM orders")
    assert not run(conn, check).passed  # an empty table is not fresh


def test_row_count_delta_catches_doubling_and_halving(conn):
    def load(today, yesterday):
        conn.execute("DELETE FROM orders")
        conn.execute("INSERT INTO orders (d) SELECT DATE '2026-09-30' FROM generate_series(1, %s)",
                     (today,))
        conn.execute("INSERT INTO orders (d) SELECT DATE '2026-09-29' FROM generate_series(1, %s)",
                     (yesterday,))
    check = ck.row_count_delta("pg_temp", "orders", "d", 1.5, layer="gold")
    load(110, 100)
    assert run(conn, check).passed
    load(200, 100)
    assert (run(conn, check).observed, run(conn, check).passed) == (2.0, False)
    load(50, 100)
    assert (run(conn, check).observed, run(conn, check).passed) == (2.0, False)
    conn.execute("DELETE FROM orders WHERE d = DATE '2026-09-29'")
    assert run(conn, check).passed  # one day only: nothing to compare yet


def test_sql_check_threshold(conn):
    conn.execute("INSERT INTO orders (id) VALUES (1), (2), (3)")
    strict = ck.sql_check("t", "gold", "orders", "SELECT count(*) FROM pg_temp.orders")
    lenient = ck.sql_check("t", "gold", "orders", "SELECT count(*) FROM pg_temp.orders",
                           threshold=5)
    assert not run(conn, strict).passed and run(conn, lenient).passed


def test_check_suite_has_every_required_kind():
    names = " ".join(c.name for c in ck.build_checks())
    for kind in ("not_null", "unique", "accepted_values", "relationships", "freshness",
                 "row_count_delta", "range", "reconciliation", "scd2_integrity"):
        assert kind in names
    assert len({c.name for c in ck.build_checks()}) == len(ck.build_checks())  # names unique


# --- running and storing ---------------------------------------------------------------------
def make(name, passes, severity=ck.BLOCKING, layer="gold"):
    return ck.Check(name, layer, "gold.x", severity,
                    lambda conn, ctx: ck.Outcome(0 if passes else 3, 0, passes, ""))


def test_results_are_stored_with_one_run_id(conn):
    report = run_checks(conn, [make("a", True), make("b", False), make("c", False, ck.WARNING)],
                        as_of=NOW)
    rows = conn.execute("SELECT check_name, status, severity, observed, threshold FROM"
                        " ops.dq_results WHERE run_id = %s ORDER BY check_name",
                        (report.run_id,)).fetchall()
    assert rows == [("a", "pass", "blocking", 0, 0), ("b", "fail", "blocking", 3, 0),
                    ("c", "fail", "warning", 3, 0)]
    assert [r.check.name for r in report.blocking_failures] == ["b"]
    assert not report.ok


def test_a_crashing_check_is_reported_and_does_not_stop_the_others(conn):
    def boom(conn, ctx):
        conn.execute("SELECT * FROM table_that_does_not_exist")
    broken = ck.Check("broken", "gold", "gold.x", ck.BLOCKING, boom)
    report = run_checks(conn, [broken, make("fine", True)], as_of=NOW)
    status = {r.check.name: r.status for r in report.results}
    assert status == {"broken": "error", "fine": "pass"}
    assert "table_that_does_not_exist" in report.results[0].detail
    assert not report.ok  # an error on a blocking check blocks


# --- assert_gold_healthy -------------------------------------------------------------------------
@pytest.fixture
def clean_slate(conn):
    run_checks(conn, [], as_of=NOW)  # make sure the ops tables exist
    conn.execute("DELETE FROM ops.dq_results")
    return conn


def test_no_run_means_not_healthy(clean_slate):
    h = assert_gold_healthy(clean_slate)
    assert not h and "no data quality run" in h.message


def test_healthy_when_only_warnings_fail(clean_slate):
    run_checks(clean_slate, [make("ok", True), make("soft", False, ck.WARNING)], as_of=NOW)
    h = assert_gold_healthy(clean_slate)
    assert h and h.healthy and h.failed_checks == []


def test_blocking_failure_gives_a_ready_to_show_warning(clean_slate):
    fresh = ck.Check("freshness:gold.fact_orders.order_ts_utc", "gold", "gold.fact_orders",
                     ck.BLOCKING, lambda c, x: ck.Outcome(50, 36, False))
    run_checks(clean_slate, [make("ok", True), fresh], as_of=NOW)
    h = assert_gold_healthy(clean_slate)
    assert not h
    assert h.message == "data quality warning: freshness check failed"
    assert h.failed_checks == ["freshness:gold.fact_orders.order_ts_utc"]


def test_only_the_latest_run_counts(clean_slate):
    run_checks(clean_slate, [make("bad", False)], as_of=NOW)
    assert not assert_gold_healthy(clean_slate)
    run_checks(clean_slate, [make("bad", True)], as_of=NOW)  # fixed, run again
    assert assert_gold_healthy(clean_slate)


def test_demo_runs_never_affect_health(clean_slate):
    run_checks(clean_slate, [make("ok", True)], as_of=NOW)
    run_checks(clean_slate, [make("bad", False)], as_of=NOW, run_kind="demo")
    assert assert_gold_healthy(clean_slate)


def test_an_old_run_can_be_treated_as_unhealthy(clean_slate):
    report = run_checks(clean_slate, [make("ok", True)], as_of=NOW)
    clean_slate.execute("UPDATE ops.dq_results SET run_ts = now() - interval '3 days'"
                        " WHERE run_id = %s", (report.run_id,))
    assert assert_gold_healthy(clean_slate)  # no age limit by default
    h = assert_gold_healthy(clean_slate, max_age_hours=24)
    assert not h and "more than 24 hours ago" in h.message


def test_run_id_is_a_uuid(clean_slate):
    report = run_checks(clean_slate, [make("ok", True)], as_of=NOW)
    assert isinstance(report.run_id, uuid.UUID)
