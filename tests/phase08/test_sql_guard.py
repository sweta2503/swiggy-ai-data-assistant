"""Phase 8a: the SQL guard. Plain code, so no database and no model are needed.

The guard must (1) accept every normal analytics query and (2) refuse every way we can think of to
do anything else. Each refusal test is an attack; each acceptance test is something the assistant
genuinely needs to write.
"""
import pytest

from swiggy_ai.security.sql_guard import check_sql, referenced_views

VIEWS = {"orders", "order_items", "refunds", "customers", "restaurants", "calendar",
         "daily_city_sales", "data_as_of"}


def guard(sql, max_rows=500):
    return check_sql(sql, VIEWS, max_rows)


# --- what a good query looks like -------------------------------------------------------------
GOOD = [
    "SELECT city, SUM(net_revenue) FROM analytics.orders GROUP BY city",
    "SELECT count(*) FROM orders",  # unqualified: resolved through search_path = analytics
    "SELECT count(*) FROM ANALYTICS.ORDERS",
    "WITH x AS (SELECT city FROM analytics.orders) SELECT * FROM x",
    "WITH a AS (SELECT 1 AS n), b AS (SELECT n FROM a) SELECT * FROM b",
    "SELECT city FROM analytics.orders UNION ALL SELECT city FROM analytics.refunds",
    "SELECT * FROM (SELECT city, count(*) c FROM analytics.orders GROUP BY 1) t WHERE c > 5",
    "SELECT o.city, r.rating FROM analytics.orders o JOIN analytics.restaurants r"
    " ON r.restaurant_id = o.restaurant_id",
    "SELECT count(*) FROM analytics.orders;",
    "SELECT d FROM generate_series(1, 5) AS d",
    "SELECT count(*) FROM analytics.orders WHERE order_date = CURRENT_DATE",
    "SELECT date_trunc('month', order_date) m, avg(delivery_minutes) FROM analytics.orders"
    " GROUP BY 1 ORDER BY 1",
    "SELECT city, rank() OVER (ORDER BY sum(net_revenue) DESC) FROM analytics.orders GROUP BY 1",
    "SELECT order_value::numeric / NULLIF(1, 0) FROM analytics.orders",
    "SELECT * FROM analytics.orders TABLESAMPLE SYSTEM (1)",
    "(SELECT city FROM analytics.orders)",
    "select CITY from Analytics.Orders",
]


@pytest.mark.parametrize("sql", GOOD)
def test_normal_analytics_queries_pass(sql):
    r = guard(sql)
    assert r.ok, r.reason
    assert "LIMIT" in r.sql.upper()


# --- the row cap ------------------------------------------------------------------------------
def test_a_missing_limit_is_added():
    r = guard("SELECT * FROM analytics.orders")
    assert r.sql.endswith("LIMIT 500") and r.changes == ("added LIMIT 500",)


def test_a_huge_limit_is_capped_and_a_small_one_is_kept():
    assert guard("SELECT * FROM analytics.orders LIMIT 999999").sql.endswith("LIMIT 500")
    kept = guard("SELECT * FROM analytics.orders LIMIT 5")
    assert kept.sql.endswith("LIMIT 5") and kept.changes == ()


def test_a_limit_hidden_in_an_expression_is_replaced():
    assert guard("SELECT * FROM analytics.orders LIMIT (SELECT 99999)").sql.endswith("LIMIT 500")


def test_the_cap_is_configurable_and_applies_to_unions():
    r = guard("SELECT city FROM analytics.orders UNION SELECT city FROM analytics.refunds", 20)
    assert r.sql.endswith("LIMIT 20")


def test_a_limit_inside_a_subquery_does_not_count_as_the_outer_limit():
    r = guard("SELECT * FROM (SELECT * FROM analytics.orders LIMIT 5) t")
    assert r.sql.endswith("LIMIT 500")


# --- statements that are not a plain read -----------------------------------------------------
BAD_STATEMENTS = {
    "delete": "DELETE FROM analytics.orders",
    "delete mixed case": "DeLeTe FrOm analytics.orders",
    "update": "UPDATE analytics.orders SET status = 'x'",
    "insert": "INSERT INTO analytics.orders (order_id) VALUES ('x')",
    "truncate": "TRUNCATE analytics.orders",
    "drop": "DROP VIEW analytics.orders",
    "create table as": "CREATE TABLE t AS SELECT * FROM analytics.orders",
    "alter": "ALTER VIEW analytics.orders RENAME TO x",
    "grant": "GRANT SELECT ON analytics.orders TO PUBLIC",
    "copy": "COPY analytics.orders TO '/tmp/x'",
    "copy program": "COPY (SELECT 1) TO PROGRAM 'cat /etc/passwd'",
    "set": "SET app.allowed_cities = 'ALL'",
    "set role": "SET ROLE etl_writer",
    "reset": "RESET ALL",
    "show": "SHOW search_path",
    "explain": "EXPLAIN SELECT 1",
    "explain analyze": "EXPLAIN ANALYZE SELECT * FROM analytics.orders",
    "do block": "DO $$ BEGIN PERFORM 1; END $$",
    "call": "CALL x()",
    "vacuum": "VACUUM",
    "begin": "BEGIN",
    "commit": "COMMIT",
    "listen": "LISTEN x",
    "prepare": "PREPARE p AS SELECT 1",
    "merge": "MERGE INTO t USING s ON true WHEN MATCHED THEN DELETE",
}


@pytest.mark.parametrize("sql", BAD_STATEMENTS.values(), ids=BAD_STATEMENTS.keys())
def test_anything_that_is_not_a_select_is_refused(sql):
    r = guard(sql)
    assert not r.ok and r.code in ("not_select", "forbidden_statement", "parse_error")


@pytest.mark.parametrize("sql", [
    "SELECT 1; DROP TABLE x",
    "SELECT 1 FROM analytics.orders; SELECT 2 FROM analytics.orders",
    "SELECT 1;; DELETE FROM analytics.orders",
    "SELECT 1\n;\nDELETE FROM analytics.orders",
])
def test_two_statements_are_refused(sql):
    assert guard(sql).code == "multiple_statements"


def test_a_data_modifying_cte_is_refused():
    r = guard("WITH d AS (DELETE FROM analytics.orders RETURNING *) SELECT * FROM d")
    assert not r.ok and r.code == "forbidden_statement"
    r = guard("WITH u AS (UPDATE analytics.orders SET status = 'x' RETURNING 1) SELECT * FROM u")
    assert not r.ok


def test_select_into_and_row_locks_are_refused():
    assert guard("SELECT * INTO t FROM analytics.orders").code == "into_clause"
    assert guard("SELECT * FROM analytics.orders FOR UPDATE").code == "lock_clause"
    assert guard("SELECT * FROM analytics.orders FOR SHARE").code == "lock_clause"


# --- comments: the classic way to hide something ----------------------------------------------
@pytest.mark.parametrize("sql", [
    "SELECT 1 FROM analytics.orders -- ; DROP TABLE x",
    "SELECT /* hi */ 1 FROM analytics.orders",
    "SELECT 1 FROM analytics.orders /* DROP TABLE x */",
    "SELECT 1 /*!50000 anything */ FROM analytics.orders",
    "SELECT 1 FROM analytics.orders\n-- trailing",
])
def test_comments_are_refused(sql):
    assert guard(sql).code == "comment"


def test_dashes_inside_a_string_are_not_a_comment():
    assert guard("SELECT count(*) FROM analytics.orders WHERE city = 'a--b'").ok


# --- relations --------------------------------------------------------------------------------
@pytest.mark.parametrize("sql", [
    "SELECT * FROM gold.fact_orders",
    "SELECT * FROM gold.customer_pii",
    "SELECT * FROM silver.orders",
    "SELECT * FROM bronze.orders",
    "SELECT * FROM ops.app_users",
    "SELECT * FROM public.orders",
    "SELECT * FROM information_schema.tables",
    "SELECT * FROM information_schema.columns",
    "SELECT * FROM pg_catalog.pg_tables",
    "SELECT * FROM pg_catalog.pg_roles",
    "SELECT * FROM pg_class",
    "SELECT * FROM pg_stat_activity",
    "SELECT * FROM swiggy.analytics.orders",
    "SELECT * FROM analytics.not_a_documented_view",
    "SELECT * FROM analytics.orders o JOIN gold.dim_city c ON true",
    "SELECT (SELECT count(*) FROM gold.fact_orders)",
    "SELECT * FROM analytics.orders WHERE order_id IN (SELECT order_id FROM silver.orders)",
    "WITH x AS (SELECT * FROM gold.fact_orders) SELECT * FROM x",
    'SELECT * FROM "gold"."fact_orders"',
    "SELECT * FROM GOLD.FACT_ORDERS",
])
def test_only_the_documented_views_can_be_read(sql):
    r = guard(sql)
    assert not r.ok and r.code == "forbidden_relation"


def test_a_cte_may_not_smuggle_a_forbidden_table_under_an_allowed_name():
    r = guard("WITH orders AS (SELECT * FROM gold.fact_orders) SELECT * FROM orders")
    assert not r.ok and r.code == "forbidden_relation"


def test_table_functions_other_than_the_harmless_ones_are_refused():
    assert not guard("SELECT * FROM dblink('x', 'select 1') AS t(a int)").ok
    assert not guard("SELECT * FROM pg_ls_dir('/')").ok


# --- functions --------------------------------------------------------------------------------
@pytest.mark.parametrize("sql", [
    "SELECT pg_sleep(10)",
    "SELECT PG_SLEEP(10)",
    'SELECT "pg_sleep"(10)',
    "SELECT pg_catalog.pg_sleep(10)",
    "SELECT (SELECT pg_sleep(10))",
    "SELECT 1 FROM analytics.orders WHERE pg_sleep(1) IS NULL",
    "WITH x AS (SELECT pg_sleep(1)) SELECT * FROM x",
    "SELECT pg_sleep_for('1 minute')",
    "SELECT pg_read_file('/etc/passwd')",
    "SELECT pg_read_binary_file('/etc/passwd')",
    "SELECT pg_ls_dir('/')",
    "SELECT pg_terminate_backend(1)",
    "SELECT pg_cancel_backend(1)",
    "SELECT pg_reload_conf()",
    "SELECT pg_stat_file('/etc/passwd')",
    "SELECT lo_import('/etc/passwd')",
    "SELECT lo_export(1, '/tmp/x')",
    "SELECT lo_get(1)",
    "SELECT set_config('app.allowed_cities', 'ALL', false)",
    "SELECT current_setting('app.allowed_cities')",
    "SELECT query_to_xml('select * from gold.customer_pii', true, false, '')",
    "SELECT table_to_xml('gold.customer_pii', true, false, '')",
    "SELECT cursor_to_xml('c', 1, true, false, '')",
    "SELECT nextval('bronze.batch_seq')",
    "SELECT setval('bronze.batch_seq', 1)",
    "SELECT version()",
    "SELECT current_user",
    "SELECT session_user",
    "SELECT current_database()",
    "SELECT has_table_privilege('gold.fact_orders', 'SELECT')",
    "SELECT to_regclass('gold.fact_orders')",
    "SELECT dblink('x', 'select 1')",
    "SELECT txid_current()",
])
def test_dangerous_functions_are_refused_wherever_they_hide(sql):
    r = guard(sql)
    assert not r.ok and r.code == "forbidden_function", (sql, r.code)


# --- malformed and odd input ------------------------------------------------------------------
@pytest.mark.parametrize("sql, code", [
    ("", "empty"), ("   ", "empty"), (";", "empty"), (" ; ; ", "empty"), (None, "empty"),
    ("SELEC FROM WHERE", "parse_error"),
    ("SELECT FROM", "parse_error"),
    ("SELECT * FROM analytics.orders WHERE", "parse_error"),
    ("x" * 9000, "too_long"),
])
def test_empty_broken_and_oversized_input_is_refused(sql, code):
    assert guard(sql).code == code


def test_refusals_return_the_original_text_and_a_readable_reason():
    r = guard("DROP TABLE x")
    assert r.sql == "DROP TABLE x" and r.code and len(r.reason) > 10


def test_an_accepted_query_is_returned_rewritten_but_equivalent():
    r = guard("select city , sum( net_revenue ) from analytics.orders group by city")
    assert r.sql.startswith("SELECT city, SUM(net_revenue) FROM analytics.orders GROUP BY city")


def test_referenced_views_ignores_ctes():
    sql = ("WITH x AS (SELECT * FROM analytics.orders) "
           "SELECT * FROM x JOIN analytics.refunds r ON true")
    assert referenced_views(sql) == {"orders", "refunds"}
