"""Phase 10, part 5: two real defects the first `make eval` run exposed, each pinned by a test.

J01  "top 5 restaurants" put restaurants with NO sales first (Postgres sorts NULLs first on DESC).
F03  item_category = 'Beverage' matched nothing (the value is 'Beverages') and the answer was 0.
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


# --- unknown values ------------------------------------------------------------------------------
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
