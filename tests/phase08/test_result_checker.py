"""Increment 08c: results that ran fine but are probably wrong.

The pure detectors are tested on their own, then the whole loop is run against the real database.
"""
import pytest

from swiggy_ai.agents.narrator import Narration
from swiggy_ai.agents.result_checker import (
    Sanity,
    check_clock_and_period,
    check_values,
    detect_period,
    find_fan_out,
)
from swiggy_ai.agents.sql_writer import SQLDraft

TODAY = "(SELECT today FROM analytics.data_as_of)"
ORDERS_ONLY = ("SELECT i.item_category AS category, SUM(o.net_revenue) AS net_revenue "
               "FROM analytics.orders o JOIN analytics.order_items i ON i.order_id = o.order_id "
               "GROUP BY i.item_category ORDER BY 1")
CORRECT = ("SELECT SUM(net_revenue) AS net_revenue FROM analytics.orders")
FAN_OUT_TOTAL = ("SELECT SUM(o.net_revenue) AS net_revenue FROM analytics.orders o "
                 "JOIN analytics.order_items i ON i.order_id = o.order_id")


# --- detecting a fan-out in the SQL -----------------------------------------------------------
@pytest.mark.parametrize("sql", [
    FAN_OUT_TOTAL,
    ORDERS_ONLY,
    "SELECT COUNT(*) AS orders FROM analytics.orders o JOIN analytics.order_items i"
    " USING (order_id)",
    "SELECT COUNT(o.order_id) FROM analytics.orders o JOIN analytics.order_items i"
    " ON i.order_id = o.order_id",
    "SELECT AVG(o.order_value) FROM analytics.orders o JOIN analytics.order_items i"
    " ON i.order_id = o.order_id",
    "SELECT SUM(net_revenue) FROM analytics.orders o JOIN analytics.order_items i"
    " ON i.order_id = o.order_id",  # unqualified: net_revenue exists only on orders
    "SELECT o.city, SUM(o.discount) FROM analytics.orders o JOIN analytics.order_items i"
    " ON o.order_id = i.order_id WHERE o.status = 'delivered' GROUP BY 1",
])
def test_order_level_figures_summed_over_item_lines_are_flagged(semantic, sql):
    assert find_fan_out(sql, semantic) is not None


@pytest.mark.parametrize("sql", [
    "SELECT SUM(net_revenue) FROM analytics.orders",  # no join at all
    "SELECT i.item_category, SUM(i.line_total) FROM analytics.orders o JOIN analytics.order_items i"
    " ON i.order_id = o.order_id GROUP BY 1",  # sums the ITEM-level figure
    "SELECT COUNT(DISTINCT o.order_id) FROM analytics.orders o JOIN analytics.order_items i"
    " ON i.order_id = o.order_id",
    "SELECT COUNT(i.order_item_id) FROM analytics.orders o JOIN analytics.order_items i"
    " ON i.order_id = o.order_id",
    "SELECT MAX(o.order_value) FROM analytics.orders o JOIN analytics.order_items i"
    " ON i.order_id = o.order_id",
    "SELECT SUM(o.net_revenue) FROM analytics.orders o JOIN analytics.restaurants r"
    " ON r.restaurant_id = o.restaurant_id",  # many orders to ONE restaurant: no fan-out
    "SELECT SUM(net_revenue) FROM analytics.orders o JOIN analytics.refunds r USING (order_id)",
    "SELECT SUM(line_total) FROM analytics.order_items",
])
def test_safe_queries_are_not_flagged(semantic, sql):
    assert find_fan_out(sql, semantic) is None


def test_a_control_query_is_built_from_orders_only_filters(semantic):
    c = find_fan_out(
        "SELECT SUM(o.net_revenue) FROM analytics.orders o JOIN analytics.order_items i"
        " ON i.order_id = o.order_id WHERE o.city = 'Pune' AND o.order_date >= '2026-03-01'",
        semantic)
    assert c.control_sql == ("SELECT SUM(o.net_revenue) FROM analytics.orders AS o WHERE "
                             "o.city = 'Pune' AND o.order_date >= '2026-03-01'")


def test_no_fair_control_exists_when_items_are_filtered(semantic):
    c = find_fan_out(ORDERS_ONLY.replace("GROUP BY", "WHERE i.item_category = 'Rice' GROUP BY"),
                     semantic)
    assert c is not None and c.control_sql is None  # flagged, but cannot be verified
    c = find_fan_out(FAN_OUT_TOTAL + " AND i.item_category = 'Rice'", semantic)
    assert c.control_sql is None
    c = find_fan_out("SELECT AVG(o.order_value) FROM analytics.orders o JOIN analytics.order_items"
                     " i ON i.order_id = o.order_id", semantic)
    assert c.control_sql is None  # an average has no simple control


# --- clock and period -------------------------------------------------------------------------
@pytest.mark.parametrize("question, period", [
    ("How many orders did we get last week?", "last_week"),
    ("revenue for the previous month", "last_month"),
    ("orders yesterday", "yesterday"),
    ("sales this month so far", "this_month"),
    ("month to date revenue", "this_month"),
    ("orders in the last 7 days", "last_7_days"),
    ("what is our revenue", None),
    ("revenue in March", None),
])
def test_period_phrases_are_recognised(question, period):
    assert detect_period(question) == period


def names(findings):
    return {f.name for f in findings}


def test_the_wall_clock_is_a_repair(semantic):
    for sql in ("SELECT count(*) FROM analytics.orders WHERE order_date = CURRENT_DATE",
                "SELECT count(*) FROM analytics.orders WHERE order_ts > now() - interval '1 day'",
                "SELECT CURRENT_TIMESTAMP"):
        assert "uses_real_clock" in names(check_clock_and_period("orders", sql, semantic)), sql


def test_a_period_without_the_data_anchor_is_a_repair_with_the_right_filter(semantic):
    sql = "SELECT count(*) FROM analytics.orders WHERE order_date >= '2026-01-01'"
    f = [x for x in check_clock_and_period("How many orders last month?", sql, semantic)
         if x.name == "period_not_anchored"][0]
    assert "date_trunc('month'" in f.hint and "analytics.data_as_of" in f.hint
    assert f.severity == "repair"


def test_a_correctly_anchored_query_passes(semantic):
    sql = f"SELECT count(*) FROM analytics.orders WHERE order_date = {TODAY} - 1"
    assert check_clock_and_period("orders yesterday", sql, semantic) == []
    assert check_clock_and_period("how many orders", "SELECT count(*) FROM analytics.orders",
                                  semantic) == []


# --- values -----------------------------------------------------------------------------------
def test_empty_null_and_capped_results_are_noticed():
    assert names(check_values(["n"], [], False, 500)) == {"empty_result"}
    assert "all_null_result" in names(check_values(["n"], [[None]], False, 500))
    assert "row_cap" in names(check_values(["n"], [[1]] * 500, True, 500))
    assert check_values(["n"], [[1]], False, 500) == []


def test_impossible_values_are_noticed():
    assert "negative_revenue" in names(check_values(["net_revenue"], [[-5.0]], False, 500))
    assert "rate_out_of_range" in names(check_values(["cancellation_rate"], [[1.4]], False, 500))
    assert "rate_out_of_range" in names(check_values(["refund_rate"], [[-0.1]], False, 500))
    assert check_values(["cancellation_rate"], [[0.08]], False, 500) == []
    assert check_values(["discount_total"], [[-5.0]], False, 500) == []  # not a revenue figure


# --- the whole loop ---------------------------------------------------------------------------
pytestmark_db = pytest.mark.db


def says(user_input):
    return Narration(answer="Here is the answer you asked for.")


def scalar(conn, sql):
    return conn.execute(sql).fetchone()[0]


@pytest.mark.db
def test_a_fan_out_is_caught_by_the_control_total_and_repaired(harness, service):
    truth = float(scalar(service, "SELECT sum(net_revenue) FROM gold.fact_orders"))
    h = harness()
    h.llm.say(SQLDraft, SQLDraft(sql=FAN_OUT_TOTAL), SQLDraft(sql=CORRECT)).say(Narration, says)
    s = h.ask("What is our total net revenue?")

    assert s.status == "answered" and s.results[0].rows == [[truth]]
    assert [(a.source, a.status, a.error_class) for a in s.attempts] == [
        ("writer", "rejected", "result_check"), ("repair", "ok", "")]
    # the writer was told exactly what was wrong, with the numbers
    retry = h.llm.calls_of(SQLDraft)[1].user_input
    assert "repeated once per item line" in retry and f"{truth:,.2f}" in retry
    assert "SUM(o.net_revenue)" in retry  # and shown its own previous query
    assert any("repaired automatically" in w for w in s.warnings)


@pytest.mark.db
def test_the_inflation_is_real_and_the_check_measured_it(harness, service):
    true_total = float(scalar(service, "SELECT sum(net_revenue) FROM gold.fact_orders"))
    inflated = float(scalar(service, "SELECT sum(o.net_revenue) FROM analytics.orders o JOIN"
                                     " analytics.order_items i USING (order_id)"))
    assert inflated > 2.5 * true_total  # the trap is a big one
    h = harness()
    h.llm.say(SQLDraft, SQLDraft(sql=FAN_OUT_TOTAL), SQLDraft(sql=CORRECT)).say(Narration, says)
    h.ask("net revenue")
    assert f"{inflated:,.2f}" in h.llm.calls_of(SQLDraft)[1].user_input


@pytest.mark.db
def test_a_fan_out_with_a_group_by_is_caught_too(harness, service):
    h = harness()
    h.llm.say(SQLDraft, SQLDraft(sql=ORDERS_ONLY), SQLDraft(sql=CORRECT)).say(Narration, says)
    s = h.ask("Revenue by item category")
    assert [a.error_class for a in s.attempts if a.status == "rejected"] == ["result_check"]


@pytest.mark.db
def test_the_check_respects_the_users_city_scope(harness, service):
    pune = float(scalar(service, "SELECT sum(f.net_revenue) FROM gold.fact_orders f JOIN"
                                 " gold.dim_city c USING (city_sk) WHERE c.city_name = 'Pune'"))
    h = harness()
    h.llm.say(SQLDraft, SQLDraft(sql=FAN_OUT_TOTAL), SQLDraft(sql=CORRECT)).say(Narration, says)
    s = h.ask("net revenue", scope="Pune")
    assert s.results[0].rows == [[pune]]
    # the control total was Pune's, not everyone's
    assert f"{pune:,.2f}" in h.llm.calls_of(SQLDraft)[1].user_input


@pytest.mark.db
def test_a_correct_join_is_left_alone(harness):
    ok = ("SELECT i.item_category, SUM(i.line_total) AS item_sales FROM analytics.order_items i"
          " WHERE i.order_status = 'delivered' GROUP BY 1 ORDER BY 2 DESC")
    h = harness()
    h.llm.say(SQLDraft, SQLDraft(sql=ok)).say(Narration, says)
    s = h.ask("Which categories sell the most?")
    assert h.llm.count(SQLDraft) == 1 and s.warnings == [] and s.checks == []


@pytest.mark.db
def test_a_join_that_does_not_change_the_total_is_not_flagged(harness):
    """Order-level sums joined to a many-to-one table are fine, and the numbers prove it."""
    sql = ("SELECT SUM(o.net_revenue) AS net_revenue FROM analytics.orders o JOIN"
           " analytics.restaurants r ON r.restaurant_id = o.restaurant_id")
    h = harness()
    h.llm.say(SQLDraft, SQLDraft(sql=sql)).say(Narration, says)
    h.ask("net revenue")
    assert h.llm.count(SQLDraft) == 1


@pytest.mark.db
def test_an_unverifiable_fan_out_is_still_repaired(harness):
    filtered = FAN_OUT_TOTAL + " WHERE i.item_category = 'Rice'"
    h = harness()
    h.llm.say(SQLDraft, SQLDraft(sql=filtered),
              SQLDraft(sql="SELECT SUM(line_total) AS net_revenue FROM analytics.order_items"
                           " WHERE item_category = 'Rice' AND order_status = 'delivered'")
              ).say(Narration, says)
    s = h.ask("Revenue from rice dishes")
    assert [a.status for a in s.attempts] == ["rejected", "ok"]
    assert s.attempts[0].error_text == "fan_out_suspected" and s.checks == []


@pytest.mark.db
def test_a_persistent_fan_out_is_not_looped_forever_and_the_user_is_warned(harness):
    h = harness(max_heal_attempts=3)
    h.llm.say(SQLDraft, SQLDraft(sql=FAN_OUT_TOTAL), SQLDraft(sql=FAN_OUT_TOTAL)).say(
        Narration, says)
    s = h.ask("net revenue")
    assert h.llm.count(SQLDraft) == 2  # one repair, then it stops asking
    assert s.status == "answered"
    assert any("treat this result with care" in w for w in s.warnings)
    assert any(c.name == "fan_out_suspected" for c in s.checks)


@pytest.mark.db
def test_with_repairs_switched_off_a_fan_out_is_only_a_warning(harness):
    h = harness(max_heal_attempts=0)
    h.llm.say(SQLDraft, SQLDraft(sql=FAN_OUT_TOTAL)).say(Narration, says)
    s = h.ask("net revenue")
    assert h.llm.count(SQLDraft) == 1 and any("with care" in w for w in s.warnings)


@pytest.mark.db
def test_the_wall_clock_is_repaired_to_the_data_anchor(harness):
    clock = "SELECT count(*) AS orders FROM analytics.orders WHERE order_date = CURRENT_DATE - 1"
    anchored = f"SELECT count(*) AS orders FROM analytics.orders WHERE order_date = {TODAY} - 1"
    h = harness()
    h.llm.say(SQLDraft, SQLDraft(sql=clock), SQLDraft(sql=anchored)).say(Narration, says)
    s = h.ask("How many orders did we get yesterday?")
    assert "data_as_of" in s.final_sql and "CURRENT_DATE" not in s.final_sql.upper()
    assert s.results[0].rows[0][0] > 0  # the clock version would have returned nothing
    assert "wall clock" in h.llm.calls_of(SQLDraft)[1].user_input


@pytest.mark.db
def test_a_hard_coded_period_is_repaired_to_a_relative_one(harness):
    literal = "SELECT count(*) AS orders FROM analytics.orders WHERE order_date >= '2026-03-01'"
    last_week = ("SELECT count(*) AS orders FROM analytics.orders WHERE order_date >= "
                 f"date_trunc('week', {TODAY}) - INTERVAL '7 days' AND order_date < "
                 f"date_trunc('week', {TODAY})")
    h = harness()
    h.llm.say(SQLDraft, SQLDraft(sql=literal), SQLDraft(sql=last_week)).say(Narration, says)
    s = h.ask("How many orders did we get last week?")
    assert "date_trunc('week'" in h.llm.calls_of(SQLDraft)[1].user_input
    assert s.status == "answered" and "data_as_of" in s.final_sql


@pytest.mark.db
def test_warnings_reach_the_user_for_odd_but_legitimate_results(harness):
    h = harness()
    h.llm.say(SQLDraft, SQLDraft(sql="SELECT 1.5 AS cancellation_rate")).say(Narration, says)
    s = h.ask("What is the cancellation rate?")
    assert s.status == "answered" and any("between 0 and 1" in w for w in s.warnings)
    assert h.llm.count(SQLDraft) == 1  # a surprising value is flagged, not "fixed"


@pytest.mark.db
def test_an_empty_result_is_not_an_error(harness):
    sql = "SELECT count(*) AS n FROM analytics.orders WHERE city = 'Atlantis' HAVING count(*) > 0"
    h = harness()
    h.llm.say(SQLDraft, SQLDraft(sql=sql)).say(Narration, lambda ui: Narration(
        answer="No data matched; Atlantis is not one of our cities."))
    s = h.ask("orders in Atlantis")
    assert s.status == "answered" and s.results[0].rows == []
    assert "No data matched" in h.llm.calls[-1].user_input or "0 rows" in h.llm.calls[-1].user_input


# --- the optional model read ------------------------------------------------------------------
@pytest.mark.db
def test_the_model_read_is_off_by_default(harness):
    h = harness()
    h.llm.say(SQLDraft, SQLDraft(sql=CORRECT)).say(Narration, says)
    h.ask("net revenue")
    assert h.llm.count(Sanity) == 0


@pytest.mark.db
def test_the_model_read_can_only_add_a_warning(harness):
    h = harness(llm_sanity_check=True)
    h.llm.say(SQLDraft, SQLDraft(sql=CORRECT)).say(
        Sanity, Sanity(looks_wrong=True, concern="This looks like revenue for the wrong period.")
    ).say(Narration, says)
    s = h.ask("What is the net revenue?")
    assert h.llm.count(SQLDraft) == 1  # never sent back for a rewrite
    assert any("wrong period" in w for w in s.warnings) and s.status == "answered"
    assert next(c for c in h.llm.calls if c.schema == "Sanity").tier == "low"


@pytest.mark.db
def test_a_reassuring_model_read_adds_nothing(harness):
    h = harness(llm_sanity_check=True)
    h.llm.say(SQLDraft, SQLDraft(sql=CORRECT)).say(Sanity, Sanity(looks_wrong=False)).say(
        Narration, says)
    assert h.ask("net revenue").warnings == []
