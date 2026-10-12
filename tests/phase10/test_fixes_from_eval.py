"""Phase 10, part 5: real defects `make eval` exposed, each pinned by a test.

J01  "top 5 restaurants" put restaurants with NO sales first (Postgres sorts NULLs first on DESC).
F03  "orders with a beverage" had three causes, found one after another:
     1. item_category = 'Beverage' matched nothing (the value is 'Beverages'): answered 0;
     2. the writer was never shown order_items (nothing in the question matched it): it guessed;
     3. shown it, it wrote COUNT(*) on a table with one row per item LINE: 49,809, not the orders.
"""
import pytest

from swiggy_ai.agents.result_checker import check_literals
from swiggy_ai.security.sql_guard import check_sql

VIEWS = {"orders", "order_items", "data_as_of"}


def guarded(sql):
    r = check_sql(sql, VIEWS)
    assert r.ok, r
    return r


def test_a_descending_sort_puts_missing_values_last():
    r = guarded("SELECT city, SUM(net_revenue) AS n FROM analytics.orders GROUP BY 1 "
                "ORDER BY n DESC LIMIT 5")
    assert "NULLS LAST" in r.sql and "NULLS FIRST" not in r.sql
    assert any("NULLs last" in c for c in r.changes)


def test_ascending_sorts_and_explicit_choices_are_left_alone():
    asc = guarded("SELECT city FROM analytics.orders GROUP BY 1 ORDER BY city")
    assert "NULLS" not in asc.sql and not any("NULLs" in c for c in asc.changes)
    chosen = guarded("SELECT city, SUM(net_revenue) n FROM analytics.orders GROUP BY 1 "
                     "ORDER BY n DESC NULLS FIRST")
    # Postgres already means NULLS FIRST for DESC, so the clause may be dropped; what matters is
    # that the guard did not override the request with NULLS LAST.
    assert "NULLS LAST" not in chosen.sql and not any("NULLs" in c for c in chosen.changes)


def test_nested_and_window_sorts_are_covered_too():
    r = guarded("SELECT * FROM (SELECT city, ROW_NUMBER() OVER (ORDER BY SUM(net_revenue) DESC) rn"
                " FROM analytics.orders GROUP BY 1) t WHERE rn <= 3 ORDER BY rn")
    assert r.sql.count("NULLS LAST") == 1


@pytest.mark.db
def test_the_top_n_really_skips_restaurants_without_sales():
    from swiggy_ai.db import connect
    sql = ("SELECT restaurant_name, SUM(order_value - discount) FILTER (WHERE status = "
           "'delivered') AS n FROM analytics.orders GROUP BY restaurant_id, restaurant_name "
           "ORDER BY n DESC LIMIT 5")
    with connect("etl", autocommit=True) as c:
        raw = c.execute(sql).fetchall()
        fixed = c.execute(guarded(sql).sql).fetchall()
    assert all(r[1] is not None for r in fixed) and len(fixed) == 5
    assert len(raw) == 5  # the unguarded query would also return 5 rows, whatever they hold


# --- unknown values ------------------------------------------------------------
def test_a_misspelt_category_is_a_repair_that_names_the_right_value(semantic):
    f = check_literals("SELECT COUNT(DISTINCT order_id) FROM analytics.order_items "
                       "WHERE item_category = 'Beverage'", semantic)
    assert [x.name for x in f] == ["unknown_value"] and f[0].severity == "repair"
    assert "Did you mean 'Beverages'" in f[0].hint and "Main Course" in f[0].hint


def test_in_lists_and_not_equal_are_checked_and_each_value_is_reported_once(semantic):
    f = check_literals("SELECT 1 FROM analytics.orders WHERE city IN ('Pune', 'Poona') "
                       "AND status <> 'deliverd' AND city <> 'Poona'", semantic)
    assert sorted(x.detail for x in f) == ["'Poona' is not a value of city",
                                           "'deliverd' is not a value of status"]


def test_correct_values_and_free_text_pass(semantic):
    ok = ("SELECT 1 FROM analytics.orders WHERE city = 'Pune' AND status = 'delivered' "
          "AND restaurant_name = 'Anything Goes' AND order_date >= '2026-01-01'")
    assert check_literals(ok, semantic) == []
    assert check_literals("SELECT 1 FROM analytics.order_items WHERE item_category = "
                          "'Beverages' AND item_name = 'Chai'", semantic) == []


def test_a_value_on_the_other_side_of_the_comparison_is_found_too(semantic):
    f = check_literals("SELECT 1 FROM analytics.orders WHERE 'Poona' = city", semantic)
    assert [x.name for x in f] == ["unknown_value"]


# --- F03, cause 2: a value only one view can hold pulls that view into the context ------------
@pytest.fixture(scope="module")
def sem():
    from swiggy_ai.semantic.loader import load_semantic
    return load_semantic()


def views(question, sem):
    from swiggy_ai.semantic.context_builder import build_context
    return [t.split(".")[1] for t in build_context(question, 2200, sem).tables]


def test_naming_a_value_that_only_one_view_holds_pulls_that_view_in(sem):
    assert "order_items" in views("How many orders included at least one beverage?", sem)
    assert "order_items" in views("How many rice orders did we get?", sem)
    assert "order_items" in views("Sales of main course dishes", sem)  # a two-word value
    assert "refunds" in views("How many late delivery refunds in Pune?", sem)


def test_a_word_from_a_multi_word_value_alone_does_not_count(sem):
    """'delivery' is part of LATE_DELIVERY and 'item' of MISSING_ITEM; both are everyday words."""
    assert "refunds" not in views("What is the average delivery time in Pune?", sem)
    assert "refunds" not in views("What are the top 5 menu items by quantity sold?", sem)


def test_a_value_several_views_allow_says_nothing_about_which_view(sem):
    from swiggy_ai.semantic.context_builder import value_owners
    owners = value_owners(sem)
    for shared in ("delivered", "pune", "vip", "new"):
        assert frozenset({shared}) not in owners, shared
    assert owners[frozenset({"beverage"})] == "analytics.order_items"


def test_the_other_golden_questions_still_get_the_views_they_got_before(sem):
    from eval.models import load_golden

    from swiggy_ai.semantic import context_builder as cb
    golden = load_golden()
    after = {q.id: cb.build_context(q.question, 2200, sem).tables for q in golden.questions}
    original = cb.value_owners
    cb.value_owners = lambda s: {}
    try:
        before = {q.id: cb.build_context(q.question, 2200, sem).tables for q in golden.questions}
    finally:
        cb.value_owners = original
    assert {i for i in after if after[i] != before[i]} == {"F03"}


# --- F03, cause 3: counting item lines when the question is about orders ----------------------
from swiggy_ai.agents.result_checker import check_count_grain  # noqa: E402

BEVERAGE_Q = "How many orders included at least one beverage?"


@pytest.mark.parametrize("sql", [
    "SELECT COUNT(*) FROM analytics.order_items WHERE item_category = 'Beverages'",
    "SELECT COUNT(oi.order_id) FROM analytics.order_items oi WHERE item_category = 'Beverages'",
    "SELECT item_category, COUNT(*) FROM analytics.order_items GROUP BY 1",
])
def test_counting_item_lines_for_a_question_about_orders_is_a_repair(sql):
    [f] = check_count_grain(BEVERAGE_Q, sql)
    assert f.name == "item_lines_counted_as_orders" and f.severity == "repair"
    assert "COUNT(DISTINCT order_id)" in f.hint and "item line" in f.hint


@pytest.mark.parametrize("question, sql", [
    (BEVERAGE_Q, "SELECT COUNT(DISTINCT order_id) FROM analytics.order_items"
                 " WHERE item_category = 'Beverages'"),
    (BEVERAGE_Q, "SELECT COUNT(*) FROM analytics.orders o WHERE EXISTS (SELECT 1 FROM"
                 " analytics.order_items i WHERE i.order_id = o.order_id)"),
    ("How many items did we sell?", "SELECT COUNT(*) FROM analytics.order_items"),
    ("How many dishes were in each order?", "SELECT COUNT(*) FROM analytics.order_items"),
    ("What is our net revenue?", "SELECT COUNT(*) FROM analytics.order_items"),
    ("Top 5 items by quantity sold in delivered orders",
     "SELECT item_name, SUM(qty) FROM analytics.order_items GROUP BY 1"),
])
def test_counting_orders_correctly_or_asking_about_items_is_left_alone(question, sql):
    assert check_count_grain(question, sql) == []


@pytest.mark.db
def test_the_writer_that_counts_item_lines_is_sent_back_and_the_orders_are_counted(harness,
                                                                                    service):
    from eval.models import EVAL_DIR

    from swiggy_ai.agents.narrator import Narration
    from swiggy_ai.agents.sql_writer import SQLDraft
    truth = service.execute((EVAL_DIR / "reference_sql" / "F03.sql").read_text()).fetchone()[0]
    h = harness()
    h.llm.say(SQLDraft,
              SQLDraft(sql="SELECT COUNT(*) AS n FROM analytics.order_items WHERE item_category"
                           " = 'Beverages'"),
              SQLDraft(sql="SELECT COUNT(DISTINCT order_id) AS n FROM analytics.order_items"
                           " WHERE item_category = 'Beverages'"))
    h.llm.default(Narration, Narration(answer="Here is the number of orders."))
    s = h.ask(BEVERAGE_Q)
    first_rows = s.attempts[0].rows_preview
    assert [(a.source, a.status) for a in s.attempts] == [("writer", "rejected"), ("repair", "ok")]
    assert first_rows and first_rows[0][0] > truth  # the first answer counted item lines: too many
    assert s.results[0].rows == [[truth]] and s.status == "answered"
