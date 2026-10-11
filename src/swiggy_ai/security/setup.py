"""Apply and verify the security setup. Needs the admin database role (it creates roles, sets
role defaults and grants privileges)."""
import logging

import psycopg
from psycopg import sql

from swiggy_ai.config import get_settings
from swiggy_ai.db import connect
from swiggy_ai.pipeline.gold import apply_security_objects
from swiggy_ai.pipeline.runner import SQL_ROOT
from swiggy_ai.semantic.loader import load_catalog

log = logging.getLogger("security")
GOOD_ROLES = ("assistant_ro", "analyst_city_role")  # the roles allowed to read the views
RAW_SCHEMAS = ("public", "bronze", "silver", "gold", "ops", "semantic")
RLS_TABLES = ["fact_orders", "fact_order_items", "fact_refunds", "daily_city_sales",
              "dim_restaurant"]


def allowed_views() -> list[str]:
    """The ONLY views the assistant may read: exactly those documented in the catalog."""
    return [t.name.split(".", 1)[1] for t in load_catalog().tables]


def role_settings(timeout_ms: int) -> dict[str, str]:
    return {
        "default_transaction_read_only": "on",
        "statement_timeout": f"{timeout_ms}ms",
        "idle_in_transaction_session_timeout": "15s",
        "lock_timeout": "2s",
        "search_path": "analytics",
        "row_security": "on",
    }


def apply_security() -> None:
    """Idempotent. Steps: roles (admin) -> RLS rules and views (table owner) -> grants and
    session defaults (admin)."""
    settings = get_settings()
    with connect("admin", autocommit=True) as admin:
        admin.execute((SQL_ROOT / "security" / "00_roles.sql").read_text())
    with connect("etl", autocommit=True) as etl:
        apply_security_objects(etl)
    with connect("admin", autocommit=True) as admin:
        for role in GOOD_ROLES:
            admin.execute(sql.SQL("REVOKE ALL ON ALL TABLES IN SCHEMA analytics FROM {}")
                          .format(sql.Identifier(role)))
            admin.execute(sql.SQL("GRANT USAGE ON SCHEMA analytics TO {}")
                          .format(sql.Identifier(role)))
            for view in allowed_views():
                admin.execute(sql.SQL("GRANT SELECT ON analytics.{} TO {}").format(
                    sql.Identifier(view), sql.Identifier(role)))
        for name, value in role_settings(settings.query_timeout_ms).items():
            admin.execute(sql.SQL("ALTER ROLE assistant_ro SET {} = {}").format(
                sql.Identifier(name), sql.Literal(value)))
    log.info("security applied")


def verify_security(conn: psycopg.Connection) -> list[str]:
    """Check the posture of the live database. Returns problems (empty = secure). Needs a
    connection that can read the catalogs (admin or etl)."""
    problems: list[str] = []

    row = conn.execute("SELECT rolsuper, rolbypassrls, rolcreaterole, rolcreatedb, rolcanlogin"
                       " FROM pg_roles WHERE rolname = 'assistant_ro'").fetchone()
    if row is None:
        return ["role assistant_ro does not exist"]
    if any(row[:4]):
        problems.append("assistant_ro has elevated attributes (superuser, bypassrls, ...)")

    # The assistant must not be able to read anything outside the allowed analytics views.
    rel_rows = conn.execute(
        "SELECT n.nspname, c.relname, c.relkind FROM pg_class c JOIN pg_namespace n ON"
        " n.oid = c.relnamespace WHERE c.relkind IN ('r', 'v', 'm', 'p', 'f')"
        " AND n.nspname NOT IN ('pg_catalog', 'information_schema', 'pg_toast')").fetchall()
    allowed = {f"analytics.{v}" for v in allowed_views()}
    for schema, name, _ in rel_rows:
        full = f"{schema}.{name}"
        for role in GOOD_ROLES:
            can = conn.execute("SELECT has_table_privilege(%s, %s, 'SELECT')",
                               (role, f'"{schema}"."{name}"')).fetchone()[0]
            if can and full not in allowed:
                problems.append(f"{role} can read {full}, which is not an allowed view")
            if not can and full in allowed:
                problems.append(f"{role} cannot read {full}, which is an allowed view")
            for priv in ("INSERT", "UPDATE", "DELETE", "TRUNCATE"):
                if conn.execute("SELECT has_table_privilege(%s, %s, %s)",
                                (role, f'"{schema}"."{name}"', priv)).fetchone()[0]:
                    problems.append(f"{role} has {priv} on {full}")
    for schema in RAW_SCHEMAS:
        if conn.execute("SELECT has_schema_privilege('assistant_ro', %s, 'USAGE')",
                        (schema,)).fetchone()[0]:
            problems.append(f"assistant_ro has USAGE on schema {schema}")
    if conn.execute("SELECT has_schema_privilege('assistant_ro', 'analytics', 'CREATE')"
                    ).fetchone()[0]:
        problems.append("assistant_ro can create objects in analytics")

    # Row-level security must be on, and forced, for every city-scoped table.
    for t in RLS_TABLES:
        flags = conn.execute("SELECT relrowsecurity, relforcerowsecurity FROM pg_class WHERE"
                             " oid = %s::regclass", (f"gold.{t}",)).fetchone()
        if not flags or not all(flags):
            problems.append(f"row-level security is not enabled and forced on gold.{t}")
        n = conn.execute("SELECT count(*) FROM pg_policies WHERE schemaname = 'gold' AND"
                         " tablename = %s", (t,)).fetchone()[0]
        if n == 0:
            problems.append(f"gold.{t} has no row-level security policy")

    # Session defaults on the assistant's login
    cfg = {k: v for k, v in (c.split("=", 1) for (c,) in conn.execute(
        "SELECT unnest(setconfig) FROM pg_db_role_setting s JOIN pg_roles r ON r.oid ="
        " s.setrole WHERE r.rolname = 'assistant_ro'"))}
    for name, expected in role_settings(get_settings().query_timeout_ms).items():
        if cfg.get(name) != expected:
            problems.append(f"assistant_ro {name} is {cfg.get(name)!r}, expected {expected!r}")

    if conn.execute("SELECT has_function_privilege('assistant_ro',"
                    " 'pg_catalog.set_config(text,text,boolean)', 'EXECUTE')").fetchone()[0]:
        problems.append("assistant_ro can call set_config(), which could widen its city scope")
    if not conn.execute("SELECT pg_has_role('assistant_ro', 'app_restricted', 'member')"
                        ).fetchone()[0]:
        problems.append("assistant_ro is not in app_restricted, so city scoping would not apply")
    return problems
