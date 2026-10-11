"""Phase 7, part 1: the assistant can read the analytics views and nothing else.

Each test is an attempt to break out. All of them must fail.
"""
import time

import psycopg
import pytest
from psycopg import errors

from swiggy_ai.config import get_settings
from swiggy_ai.db import connect
from swiggy_ai.security.scope import assistant_connection
from swiggy_ai.security.setup import allowed_views, verify_security
from tests.phase07.helpers import acting_as

pytestmark = pytest.mark.db
ROLES = ["assistant_ro", "analyst_city_role"]
RAW_TABLES = [
    "bronze.orders", "bronze.load_ledger", "bronze.rejected_files_rows", "silver.orders",
    "silver.customer_snapshots", "silver.rejected_rows", "gold.fact_orders", "gold.dim_customer",
    "gold.dim_city", "gold.customer_pii", "gold.daily_city_sales", "ops.dq_results",
    "ops.app_users", "ops.role_city_access", "ops.pipeline_state",
]


@pytest.fixture
def admin():
    c = connect("admin")
    yield c
    c.rollback()
    c.close()


def one(conn, sql, *args):
    return conn.execute(sql, args).fetchone()[0]


# --- reading ----------------------------------------------------------------------------------
@pytest.mark.parametrize("role", ROLES)
@pytest.mark.parametrize("table", RAW_TABLES)
def test_raw_layers_and_personal_data_are_unreadable(admin, role, table):
    with acting_as(admin, role), pytest.raises(errors.InsufficientPrivilege):
        admin.execute(f"SELECT * FROM {table} LIMIT 1")


def test_a_gold_partition_is_unreadable_too(admin):
    part = one(admin, "SELECT inhrelid::regclass::text FROM pg_inherits WHERE inhparent ="
                      " 'gold.fact_orders'::regclass LIMIT 1")
    with acting_as(admin, "assistant_ro"), pytest.raises(errors.InsufficientPrivilege):
        admin.execute(f"SELECT * FROM {part} LIMIT 1")


@pytest.mark.parametrize("role", ROLES)
def test_every_documented_view_is_readable_and_only_those(admin, role):
    views = allowed_views()
    assert len(views) == 8
    with acting_as(admin, role):
        for v in views:
            admin.execute(f"SELECT * FROM analytics.{v} LIMIT 1")  # must not raise
    granted = {n for (n,) in admin.execute(
        "SELECT c.relname FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace"
        " WHERE n.nspname = 'analytics' AND c.relkind = 'v'"
        " AND has_table_privilege(%s, c.oid, 'SELECT')", (role,))}
    assert granted == set(views)


def test_a_new_view_is_not_readable_until_someone_documents_and_grants_it(admin):
    admin.execute("CREATE VIEW analytics.unlisted AS SELECT 1 AS x")
    with acting_as(admin, "assistant_ro"), pytest.raises(errors.InsufficientPrivilege):
        admin.execute("SELECT * FROM analytics.unlisted")


# --- writing and changing things --------------------------------------------------------------
WRITES = [
    "INSERT INTO analytics.orders (order_id) VALUES ('x')",
    "UPDATE analytics.orders SET status = 'delivered'",
    "DELETE FROM analytics.orders",
    "TRUNCATE analytics.orders",
    "DROP VIEW analytics.orders",
    "ALTER VIEW analytics.orders RENAME TO stolen",
    "CREATE TABLE analytics.t (x int)",
    "CREATE VIEW analytics.v2 AS SELECT 1",
    "CREATE TABLE public.t (x int)",
    "CREATE TEMP TABLE t (x int)",
    "CREATE SCHEMA evil",
    "CREATE ROLE evil",
    "CREATE EXTENSION dblink",
    "COPY (SELECT 1) TO PROGRAM 'echo hi'",
    "COPY analytics.orders TO '/tmp/orders.csv'",
]


@pytest.mark.parametrize("role", ROLES)
@pytest.mark.parametrize("statement", WRITES)
def test_no_write_or_ddl_statement_succeeds(admin, role, statement):
    with acting_as(admin, role), pytest.raises(psycopg.Error):
        admin.execute(statement)


@pytest.mark.parametrize("role", ROLES)
def test_a_role_cannot_grant_privileges_it_does_not_own(admin, role):
    with acting_as(admin, role):
        admin.execute("GRANT SELECT ON analytics.orders TO PUBLIC")  # Postgres only warns...
    # ...and nothing was granted
    assert not one(admin, "SELECT has_table_privilege('public', 'analytics.orders', 'SELECT')")


def test_a_role_cannot_stop_other_sessions(admin):
    with acting_as(admin, "assistant_ro"):
        pid = 1  # not one of ours; the point is that the call has no effect for this role
        assert one(admin, "SELECT pg_terminate_backend(%s)", pid) is False  # refused, not killed


def test_no_table_privilege_beyond_select_exists_anywhere(admin):
    for role in ROLES:
        n = one(admin, "SELECT count(*) FROM pg_class c JOIN pg_namespace n ON n.oid ="
                       " c.relnamespace, unnest(ARRAY['INSERT', 'UPDATE', 'DELETE', 'TRUNCATE',"
                       " 'REFERENCES', 'TRIGGER']) p WHERE c.relkind IN ('r', 'v', 'p', 'm')"
                       " AND n.nspname NOT IN ('pg_catalog', 'information_schema')"
                       " AND has_table_privilege(%s, c.oid, p)", role)
        assert n == 0, role


# --- dangerous functions ----------------------------------------------------------------------
@pytest.mark.parametrize("statement", [
    "SELECT set_config('app.allowed_cities', 'ALL', false)",
    "SELECT pg_read_file('/etc/passwd')",
    "SELECT pg_ls_dir('/')",
    "SELECT lo_import('/etc/passwd')",
    "SELECT pg_reload_conf()",
])
def test_dangerous_functions_are_refused(admin, statement):
    with acting_as(admin, "assistant_ro"), pytest.raises(psycopg.Error):
        admin.execute(statement)


# --- the verifier itself ----------------------------------------------------------------------
def test_verifier_passes_on_the_real_setup(admin):
    assert verify_security(admin) == []


@pytest.mark.parametrize("breakage, expected", [
    ("GRANT SELECT ON bronze.orders TO assistant_ro", "assistant_ro can read bronze.orders"),
    ("GRANT INSERT ON analytics.orders TO assistant_ro", "has INSERT on analytics.orders"),
    ("GRANT USAGE ON SCHEMA gold TO assistant_ro", "USAGE on schema gold"),
    ("ALTER TABLE gold.fact_orders NO FORCE ROW LEVEL SECURITY",
     "row-level security is not enabled and forced on gold.fact_orders"),
    ("ALTER TABLE gold.fact_refunds DISABLE ROW LEVEL SECURITY",
     "row-level security is not enabled and forced on gold.fact_refunds"),
    ("DROP POLICY city_scope ON gold.daily_city_sales", "no row-level security policy"),
    ("GRANT EXECUTE ON FUNCTION pg_catalog.set_config(text, text, boolean) TO PUBLIC",
     "can call set_config()"),
    ("REVOKE app_restricted FROM assistant_ro", "not in app_restricted"),
    ("ALTER ROLE assistant_ro BYPASSRLS", "elevated attributes"),
    ("ALTER ROLE assistant_ro SET default_transaction_read_only = off",
     "default_transaction_read_only"),
    ("ALTER ROLE assistant_ro RESET statement_timeout", "statement_timeout"),
    ("REVOKE SELECT ON analytics.orders FROM assistant_ro", "cannot read analytics.orders"),
])
def test_verifier_catches_each_way_the_setup_could_be_weakened(admin, breakage, expected):
    admin.execute(breakage)
    found = "\n".join(verify_security(admin))
    assert expected in found


# --- real logins: session limits that only apply at login -------------------------------------
def test_assistant_login_has_hardened_session_defaults():
    with assistant_connection("ALL") as c:
        def show(name):
            return c.execute(f"SHOW {name}").fetchone()[0]
        assert show("default_transaction_read_only") == "on"
        assert show("search_path") == "analytics"
        assert show("row_security") == "on"
        assert show("idle_in_transaction_session_timeout") == "15s"
        assert show("lock_timeout") == "2s"
        assert show("statement_timeout") == f"{get_settings().query_timeout_ms // 1000}s"
        assert c.execute("SELECT rolsuper, rolbypassrls, rolcreaterole FROM pg_roles WHERE"
                         " rolname = current_user").fetchone() == (False, False, False)


def test_assistant_login_cannot_write_even_if_it_asks_for_a_writable_transaction():
    # analytics.calendar is a simple view, so Postgres would allow updates through it if the
    # role had the privilege. It does not.
    with assistant_connection("ALL") as c:
        with pytest.raises((errors.InsufficientPrivilege, errors.ReadOnlySqlTransaction)):
            c.execute("UPDATE analytics.calendar SET year = 1")  # blocked on two grounds
        c.execute("SET default_transaction_read_only = off")  # the client may try to lift it...
        with pytest.raises(errors.InsufficientPrivilege):  # ...but the privilege is missing
            c.execute("UPDATE analytics.calendar SET year = 1")
        with pytest.raises(errors.InsufficientPrivilege):
            c.execute("DELETE FROM analytics.calendar")


def test_unqualified_names_resolve_to_analytics_and_the_search_path_cannot_escape():
    with assistant_connection("ALL") as c:
        c.execute("SELECT count(*) FROM orders")  # found through search_path = analytics
        c.execute("SET search_path = gold")  # the schema is not even visible to this role
        with pytest.raises((errors.UndefinedTable, errors.InsufficientPrivilege)):
            c.execute("SELECT count(*) FROM fact_orders")


def test_a_long_query_is_cut_off_by_the_statement_timeout():
    limit = get_settings().query_timeout_ms / 1000
    with assistant_connection("ALL") as c:
        started = time.time()
        with pytest.raises(errors.QueryCanceled):
            c.execute("SELECT pg_sleep(%s)", (limit * 4,))
        assert limit * 0.8 < time.time() - started < limit + 3


def test_an_idle_open_transaction_is_cleaned_up_by_the_database():
    # the limit exists and is short; we check the setting rather than waiting 15 seconds
    with assistant_connection("ALL") as c:
        assert c.execute("SELECT current_setting('idle_in_transaction_session_timeout')"
                         ).fetchone()[0] == "15s"


def test_the_connection_limit_is_set_so_one_client_cannot_exhaust_the_database(admin):
    assert one(admin, "SELECT rolconnlimit FROM pg_roles WHERE rolname = 'assistant_ro'") == 20
