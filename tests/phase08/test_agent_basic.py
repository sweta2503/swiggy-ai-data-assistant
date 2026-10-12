"""Increment 08a: question -> schema -> SQL -> guard -> cost -> run -> narrate.

The model is scripted (no OpenAI calls); the database is real, and the queries run as the real
read-only, city-scoped assistant login.
"""
import functools

import pytest

from swiggy_ai.agents.narrator import Narration, fmt
from swiggy_ai.agents.sql_writer import SQLDraft
from swiggy_ai.db import connect

pytestmark = pytest.mark.db
NET = ("SELECT SUM(order_value - discount) FILTER (WHERE status = 'delivered') AS net_revenue "
       "FROM analytics.orders")
TODAY = "(SELECT today FROM analytics.data_as_of)"
NET_LAST_MONTH = (NET + f" WHERE city = 'Pune' AND order_date >= date_trunc('month', {TODAY})"
                  f" - INTERVAL '1 month' AND order_date < date_trunc('month', {TODAY})")


@functools.cache
def total_net_revenue() -> float:
    """The truth, read from gold (a number typed into a test fits only one day's data)."""
    with connect("etl", autocommit=True) as c:
        return float(c.execute("SELECT sum(net_revenue) FROM gold.fact_orders").fetchone()[0])


def scalar(conn, sql):
    return conn.execute(sql).fetchone()[0]


def says_nothing_numeric(user_input):
    return Narration(answer="Here is the net revenue you asked for.")


def says_revenue(value):
    return lambda user_input: Narration(answer=f"Net revenue was ₹{value:,.2f} in total.")


def test_a_question_goes_all_the_way_to_a_checked_answer(harness, service):
    truth = float(scalar(service, "SELECT sum(net_revenue) FROM gold.fact_orders"))
    h = harness()
    h.llm.say(SQLDraft, SQLDraft(sql=NET, assumptions=["all time"])).say(
        Narration, says_revenue(truth))

    s = h.ask("What is our net revenue?")

    assert s.status == "answered" and f"{truth:,.2f}" in s.answer
    assert s.results[0].rows == [[truth]]  # the number came from the database, not the model
    assert s.final_sql.endswith("LIMIT 500")  # the guard added a row cap
    assert [a.status for a in s.attempts] == ["ok"]
    assert s.usage.calls == 3 and s.usage.tokens_in == 300  # router, SQL writer, narrator
    assert set(s.usage.by_step) == {"router", "sql_writer", "narrator"}
    assert s.warnings == []


def test_models_are_used_by_tier_cheap_for_wording_stronger_for_sql(harness):
    h = harness()
    h.llm.say(SQLDraft, SQLDraft(sql=NET)).say(Narration, says_revenue(total_net_revenue()))
    h.ask("net revenue")
    assert {c.schema: c.tier for c in h.llm.calls} == {"RouteDecision": "low",
                                                       "SQLDraft": "medium", "Narration": "low"}


def test_the_writer_is_given_the_semantic_context_for_this_question(harness):
    h = harness()
    h.llm.say(SQLDraft, SQLDraft(sql=NET_LAST_MONTH)).say(Narration, says_nothing_numeric)
    h.ask("What was our net revenue last month in Pune?")
    prompt = h.llm.calls_of(SQLDraft)[0].user_input
    assert "SUM(order_value - discount) FILTER (WHERE status = 'delivered')" in prompt
    assert "analytics.data_as_of" in prompt and "Asia/Kolkata" in prompt
    assert "Do not:" in prompt
    assert "gold." not in prompt and "silver." not in prompt  # it never hears of the raw layers
    assert "CURRENT_DATE" in h.llm.calls_of(SQLDraft)[0].instructions  # and is told not to use it


def test_the_numbers_in_the_answer_must_come_from_the_result(harness):
    h = harness()
    h.llm.say(SQLDraft, SQLDraft(sql=NET)).say(
        Narration,
        Narration(answer="Net revenue was ₹99,999,999.00."),  # invented
        Narration(answer="Net revenue was about ₹70,000,000."))  # invented again
    s = h.ask("net revenue")
    assert h.llm.count(Narration) == 2  # it was asked once more, firmly
    assert "99,999,999" not in s.answer and "70,000,000" not in s.answer
    assert f"net_revenue: {fmt(total_net_revenue())}" in s.answer  # the plain figures from the data
    assert any("could not be verified" in w for w in s.warnings)


def test_a_narration_that_fixes_itself_on_the_second_try_is_accepted(harness):
    h = harness()
    h.llm.say(SQLDraft, SQLDraft(sql=NET)).say(
        Narration, Narration(answer="Revenue was ₹12,345."),
        Narration(answer=f"Net revenue was ₹{total_net_revenue():,.2f}."))
    s = h.ask("net revenue")
    assert s.answer == f"Net revenue was ₹{total_net_revenue():,.2f}." and s.warnings == []
    assert "not in the result" in h.llm.calls[-1].user_input


def test_a_percentage_can_be_written_from_a_ratio(harness):
    h = harness()
    h.llm.say(SQLDraft, SQLDraft(sql="SELECT 0.0402::numeric AS refund_rate")).say(
        Narration, Narration(answer="The refund rate is 4.02%."))
    s = h.ask("refund rate")
    assert s.answer == "The refund rate is 4.02%." and s.warnings == []


# --- the guard --------------------------------------------------------------------------------
@pytest.mark.parametrize("sql", [
    "DELETE FROM analytics.orders",
    "SELECT * FROM gold.fact_orders",
    "SELECT pg_sleep(30)",
    "SELECT 1; DROP VIEW analytics.orders",
    "SELECT * FROM analytics.orders -- hidden",
])
def test_unsafe_sql_from_the_model_never_reaches_the_database(harness, service, sql):
    before = scalar(service, "SELECT count(*) FROM gold.fact_orders")
    h = harness()
    h.llm.say(SQLDraft, SQLDraft(sql=sql))
    s = h.ask("anything")
    assert s.status == "refused" and "safety guard" in s.answer
    assert [a.status for a in s.attempts] == ["rejected"] and s.attempts[0].error_class == "guard"
    assert h.llm.count(Narration) == 0  # no model was asked to explain a result that was not run
    assert scalar(service, "SELECT count(*) FROM gold.fact_orders") == before


def test_a_trailing_semicolon_from_the_model_is_tidied_not_refused(harness):
    h = harness()
    h.llm.say(SQLDraft, SQLDraft(sql=NET + ";")).say(Narration, says_revenue(total_net_revenue()))
    assert h.ask("net revenue").status == "answered"


# --- running it -------------------------------------------------------------------------------
def test_the_row_cap_is_enforced_and_reported(harness):
    h = harness(max_rows=50)
    h.llm.say(SQLDraft, SQLDraft(sql="SELECT order_id, city FROM analytics.orders")).say(
        Narration, Narration(answer="Here are 50 orders."))
    s = h.ask("list orders")
    assert len(s.results[0].rows) == 50 and s.results[0].truncated
    assert "cut at the row limit" in h.llm.calls[-1].user_input


def test_a_database_error_is_classified_and_reported(harness):
    h = harness(max_heal_attempts=0)  # (the repair loop has its own tests)
    h.llm.say(SQLDraft, SQLDraft(sql="SELECT total_amount FROM analytics.orders"))
    s = h.ask("revenue")
    assert s.status == "failed"
    assert s.attempts[0].error_class == "undefined_column"
    assert "total_amount" in s.attempts[0].error_text


@pytest.mark.parametrize("sql, expected", [
    ("SELEC city FROM analytics.orders", "syntax"),  # not even parseable: an ordinary mistake
    ("SELECT city FROM analytics.orders GROUP", "syntax"),
    ("SELECT nope FROM analytics.orders", "undefined_column"),
    ("SELECT city, count(*) FROM analytics.orders GROUP BY restaurant_id", "grouping_error"),
    ("SELECT order_value + city FROM analytics.orders", "datatype_mismatch"),
    ("SELECT DATEADD(day, 1, order_date) FROM analytics.orders", "undefined_column"),  # `day`
    ("SELECT GETDATE() FROM analytics.orders", "datatype_mismatch"),  # no such function
    ("SELECT city FROM analytics.orders o JOIN analytics.order_items i ON true"
     " WHERE city = 'x' AND order_id = 'x'", "ambiguous_column"),
    ("SELECT 1 / 0", "other"),
])
def test_database_errors_get_the_right_class(harness, sql, expected):
    h = harness(max_heal_attempts=0)
    h.llm.say(SQLDraft, SQLDraft(sql=sql))
    s = h.ask("anything")
    assert s.attempts[-1].error_class == expected, s.attempts[-1].error_text


def test_a_query_that_runs_too_long_is_cut_off(harness):
    # a huge cost limit lets the expensive plan through to the timeout
    h = harness(query_timeout_ms=500, max_plan_cost=1e18, max_heal_attempts=0)
    slow = ("SELECT count(*) FROM analytics.orders a CROSS JOIN analytics.orders b "
            "CROSS JOIN analytics.orders c")
    h.llm.say(SQLDraft, SQLDraft(sql=slow))
    s = h.ask("a very heavy question")
    assert s.status == "failed" and s.attempts[-1].error_class == "timeout"


# --- the cost check ---------------------------------------------------------------------------
def test_an_expensive_plan_gets_one_chance_to_be_rewritten_then_a_warning(harness):
    h = harness(max_plan_cost=1)  # everything is "too expensive"
    h.llm.say(SQLDraft, SQLDraft(sql=NET), SQLDraft(sql=NET + " WHERE 1 = 1")).say(
        Narration, says_revenue(total_net_revenue()))
    s = h.ask("net revenue")
    assert h.llm.count(SQLDraft) == 2  # asked exactly once more, not in a loop
    assert "estimated cost" in h.llm.calls_of(SQLDraft)[1].user_input  # the model was told why
    assert "Do NOT change what the question asks" in h.llm.calls_of(SQLDraft)[1].user_input
    assert [a.error_class for a in s.attempts if a.status == "rejected"] == ["cost"]
    assert s.status == "answered" and any("expensive" in w for w in s.warnings)


def test_a_period_in_the_question_but_not_in_the_query_gets_one_rewrite(harness):
    h = harness()
    h.llm.say(SQLDraft, SQLDraft(sql=NET), SQLDraft(sql=NET_LAST_MONTH)).say(
        Narration, says_nothing_numeric)
    s = h.ask("What was our net revenue last month?")
    assert h.llm.count(SQLDraft) == 2 and "no date filter" in h.llm.calls_of(SQLDraft)[1].user_input
    assert s.status == "answered" and "date_trunc('month'" in s.final_sql.lower()


def test_a_correct_relative_period_is_not_mistaken_for_a_full_scan(harness):
    h = harness()
    h.llm.say(SQLDraft, SQLDraft(sql=NET_LAST_MONTH)).say(Narration, says_nothing_numeric)
    s = h.ask("What was our net revenue last month?")
    assert h.llm.count(SQLDraft) == 1 and s.warnings == []


def test_an_all_time_question_is_left_alone(harness):
    h = harness()
    h.llm.say(SQLDraft, SQLDraft(sql=NET)).say(Narration, says_revenue(total_net_revenue()))
    h.ask("What is our total net revenue?")
    assert h.llm.count(SQLDraft) == 1


def test_a_cheap_query_is_not_questioned(harness):
    h = harness()
    h.llm.say(SQLDraft, SQLDraft(sql=NET)).say(Narration, says_revenue(total_net_revenue()))
    s = h.ask("net revenue")
    assert h.llm.count(SQLDraft) == 1 and s.warnings == []


def test_a_planning_error_is_caught_before_execution(harness):
    h = harness(max_heal_attempts=0)
    h.llm.say(SQLDraft, SQLDraft(sql="SELECT nope FROM analytics.orders"))
    s = h.ask("anything")
    assert len(s.attempts) == 1 and s.attempts[0].error_class == "undefined_column"


# --- city scope -------------------------------------------------------------------------------
def test_the_query_runs_with_the_users_city_scope(harness, service):
    blr = float(scalar(service, "SELECT sum(net_revenue) FROM gold.fact_orders f JOIN"
                                " gold.dim_city c USING (city_sk) WHERE c.city_name = 'Bengaluru'"))
    h = harness()
    h.llm.say(SQLDraft, SQLDraft(sql=NET)).say(Narration, says_revenue(blr))
    s = h.ask("What is our total revenue?", scope="Bengaluru")
    assert s.results[0].rows == [[blr]]  # the question named no city; the scope did the work


def test_a_run_with_no_scope_is_refused_outright(harness):
    h = harness()
    h.llm.say(SQLDraft, SQLDraft(sql=NET))
    with pytest.raises(PermissionError):
        h.ask("net revenue", scope="")


# --- data quality -----------------------------------------------------------------------------
def test_a_failing_data_quality_check_is_shown_beside_the_answer(harness):
    h = harness()
    h.rt.health_check = lambda: ["data quality warning: freshness check failed"]
    h.llm.say(SQLDraft, SQLDraft(sql=NET)).say(Narration, says_revenue(total_net_revenue()))
    s = h.ask("net revenue")
    assert s.status == "answered" and s.warnings == ["data quality warning: freshness check failed"]


# --- the cost helpers on their own ------------------------------------------------------------
@pytest.mark.parametrize("sql, lacks", [
    ("SELECT count(*) FROM analytics.orders", True),
    ("SELECT count(*) FROM analytics.orders WHERE city = 'Pune'", True),
    ("SELECT count(*) FROM analytics.orders WHERE order_date >= '2026-01-01'", False),
    ("SELECT count(*) FROM analytics.refunds WHERE refund_date = '2026-01-01'", False),
    ("SELECT count(*) FROM analytics.orders o JOIN analytics.calendar c ON c.date = o.order_date"
     " WHERE c.is_weekend", True),
    ("SELECT count(*) FROM analytics.orders o JOIN analytics.calendar c ON c.date = o.order_date"
     " AND c.month_name = 'March' WHERE o.order_date >= '2026-03-01'", False),
    ("SELECT count(*) FILTER (WHERE order_date >= '2026-03-01') FROM analytics.orders", False),
    ("SELECT sum(CASE WHEN order_date >= '2026-03-01' THEN 1 ELSE 0 END) FROM analytics.orders",
     False),
    ("WITH b AS (SELECT date_trunc('month', today) AS m FROM analytics.data_as_of)"
     " SELECT count(*) FROM analytics.orders o, b WHERE o.order_date >= b.m", False),
    ("WITH b AS (SELECT date_trunc('month', today) AS m FROM analytics.data_as_of)"
     " SELECT count(*) FILTER (WHERE r.refund_date >= b.m) FROM analytics.refunds r CROSS JOIN b",
     False),
    ("SELECT count(*) FROM analytics.restaurants", False),  # small, not a dated view
    ("SELECT today FROM analytics.data_as_of", False),
])
def test_missing_date_filter_detection(sql, lacks):
    from swiggy_ai.agents.cost_check import lacks_date_filter
    assert lacks_date_filter(sql) is lacks
