"""The data quality checks.

A check measures one thing and compares it to a threshold:

    observed  = what we measured (number of bad rows, hours late, a ratio ...)
    threshold = the most we accept
    pass      = observed <= threshold

Blocking checks stop a publish and make `assert_gold_healthy()` return False. Warning checks are
reported but do not block.
"""
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime

import psycopg
from psycopg import sql

BLOCKING, WARNING = "blocking", "warning"


@dataclass
class Context:
    """Facts a check may need that are not in the database."""
    as_of: datetime  # "now", as far as freshness is concerned (timezone-aware)


@dataclass
class Outcome:
    observed: float | None
    threshold: float
    passed: bool
    detail: str = ""


@dataclass
class Check:
    name: str
    layer: str  # bronze | silver | gold
    table: str  # for the report, e.g. "gold.fact_orders"
    severity: str
    run: Callable[[psycopg.Connection, Context], Outcome]
    description: str = ""
    tags: list[str] = field(default_factory=list)


def _table(schema: str, table: str) -> sql.Composable:
    return sql.Identifier(schema, table)


def _count(conn: psycopg.Connection, query: sql.Composable, params: tuple = ()) -> float:
    value = conn.execute(query, params or None).fetchone()[0]
    return float(value or 0)


def _at_most(observed: float, threshold: float, detail: str = "") -> Outcome:
    return Outcome(observed, threshold, observed <= threshold, detail)


# ---------------------------------------------------------------- building blocks ---------------
def sql_check(name: str, layer: str, table: str, query: str, *, threshold: float = 0,
              severity: str = BLOCKING, description: str = "") -> Check:
    """A check defined by one SQL statement that returns one number (the bad-row count)."""
    def run(conn: psycopg.Connection, ctx: Context) -> Outcome:
        return _at_most(_count(conn, sql.SQL(query)), threshold)
    return Check(name, layer, table, severity, run, description)


def not_null(schema: str, table: str, column: str, *, layer: str,
             severity: str = BLOCKING) -> Check:
    def run(conn, ctx):
        q = sql.SQL("SELECT count(*) FROM {} WHERE {} IS NULL").format(
            _table(schema, table), sql.Identifier(column))
        return _at_most(_count(conn, q), 0)
    return Check(f"not_null:{schema}.{table}.{column}", layer, f"{schema}.{table}", severity,
                 run, f"{column} must never be empty")


def unique(schema: str, table: str, columns: list[str], *, layer: str,
           severity: str = BLOCKING) -> Check:
    """observed = rows beyond the first for each key (0 means no duplicates)."""
    def run(conn, ctx):
        cols = sql.SQL(", ").join(sql.Identifier(c) for c in columns)
        q = sql.SQL("SELECT coalesce(sum(n - 1), 0) FROM (SELECT count(*) n FROM {} GROUP BY {}"
                    " HAVING count(*) > 1) d").format(_table(schema, table), cols)
        return _at_most(_count(conn, q), 0)
    return Check(f"unique:{schema}.{table}.{'+'.join(columns)}", layer, f"{schema}.{table}",
                 severity, run, f"{', '.join(columns)} must identify exactly one row")


def accepted_values(schema: str, table: str, column: str, values: list[str], *, layer: str,
                    severity: str = BLOCKING) -> Check:
    def run(conn, ctx):
        q = sql.SQL("SELECT count(*) FROM {} WHERE {} IS NOT NULL AND {} <> ALL(%s)").format(
            _table(schema, table), sql.Identifier(column), sql.Identifier(column))
        return _at_most(_count(conn, q, (values,)), 0)
    return Check(f"accepted_values:{schema}.{table}.{column}", layer, f"{schema}.{table}",
                 severity, run, f"{column} must be one of {values}")


def relationships(child: tuple[str, str, str], parent: tuple[str, str, str], *, layer: str,
                  ignore_if: str | None = None, severity: str = BLOCKING) -> Check:
    """Every child value must exist in the parent. Each side is (schema, table, column).
    `ignore_if` is a fixed SQL condition on alias c (the child) for known, explained exceptions."""
    (cs, ct, cc), (ps, pt, pc) = child, parent

    def run(conn, ctx):
        q = sql.SQL("SELECT count(*) FROM {c} c WHERE c.{cc} IS NOT NULL AND NOT EXISTS ("
                    "SELECT 1 FROM {p} p WHERE p.{pc} = c.{cc})").format(
            c=_table(cs, ct), p=_table(ps, pt), cc=sql.Identifier(cc), pc=sql.Identifier(pc))
        if ignore_if:
            q = sql.SQL("{} AND NOT ({})").format(q, sql.SQL(ignore_if))
        return _at_most(_count(conn, q), 0)
    return Check(f"relationships:{cs}.{ct}.{cc}->{ps}.{pt}.{pc}", layer, f"{cs}.{ct}", severity,
                 run, f"every {cc} must exist in {ps}.{pt}.{pc}")


def value_range(schema: str, table: str, column: str, low: float, high: float, *, layer: str,
                severity: str = BLOCKING) -> Check:
    def run(conn, ctx):
        q = sql.SQL("SELECT count(*) FROM {} WHERE {} IS NOT NULL AND ({} < %s OR {} > %s)").format(
            _table(schema, table), *(sql.Identifier(column),) * 3)
        return _at_most(_count(conn, q, (low, high)), 0)
    return Check(f"range:{schema}.{table}.{column}", layer, f"{schema}.{table}", severity, run,
                 f"{column} must be between {low} and {high}")


def freshness(schema: str, table: str, column: str, max_hours: float, *, layer: str,
              severity: str = BLOCKING) -> Check:
    """observed = hours between the newest row and `as_of`."""
    def run(conn, ctx):
        q = sql.SQL("SELECT extract(epoch FROM (%s::timestamptz - max({}))) / 3600 FROM {}"
                    ).format(sql.Identifier(column), _table(schema, table))
        hours = conn.execute(q, (ctx.as_of,)).fetchone()[0]
        if hours is None:
            return Outcome(None, max_hours, False, "table is empty")
        return _at_most(round(float(hours), 2), max_hours, f"newest row is {hours:.1f}h old")
    return Check(f"freshness:{schema}.{table}.{column}", layer, f"{schema}.{table}", severity,
                 run, f"newest {column} must be within {max_hours} hours of the expected time")


def row_count_delta(schema: str, table: str, date_column: str, max_ratio: float, *, layer: str,
                    severity: str = WARNING) -> Check:
    """Latest day vs the day before. observed = the bigger of (today/yesterday, yesterday/today),
    so a doubled load and a halved load both show up as 2.0."""
    def run(conn, ctx):
        q = sql.SQL("SELECT d, count(*) FROM (SELECT {c}::date d FROM {t}) x GROUP BY d"
                    " ORDER BY d DESC LIMIT 2").format(c=sql.Identifier(date_column),
                                                       t=_table(schema, table))
        rows = conn.execute(q).fetchall()
        if len(rows) < 2:
            return Outcome(1.0, max_ratio, True, "not enough history to compare")
        (d_new, today), (d_old, yesterday) = rows
        ratio = max(today / yesterday, yesterday / today)
        return _at_most(round(ratio, 2), max_ratio,
                        f"{d_new}: {today} rows, {d_old}: {yesterday} rows")
    return Check(f"row_count_delta:{schema}.{table}", layer, f"{schema}.{table}", severity, run,
                 f"latest day should be within {max_ratio}x of the day before")


# ---------------------------------------------------------------- the check suite --------------
ORDER_STATUSES = ["delivered", "cancelled", "refunded", "failed"]
REFUND_REASONS = ["LATE_DELIVERY", "MISSING_ITEM", "WRONG_ITEM", "QUALITY_ISSUE", "SPOILED_FOOD"]
MAX_FRESHNESS_HOURS = 36
MAX_DAY_OVER_DAY_RATIO = 1.8
VALID_DELIVERY_MINUTES = (1, 240)  # the same rule silver applies


def silver_checks(silver: str = "silver") -> list[Check]:
    s = silver
    checks = [not_null(s, "orders", c, layer="silver")
              for c in ("order_id", "customer_id", "restaurant_id", "order_ts_utc", "status")]
    checks += [
        unique(s, "orders", ["order_id"], layer="silver"),
        unique(s, "order_items", ["order_item_id"], layer="silver"),
        accepted_values(s, "orders", "status", ORDER_STATUSES, layer="silver"),
        relationships((s, "order_items", "order_id"), (s, "orders", "order_id"), layer="silver"),
        relationships((s, "refunds", "order_id"), (s, "orders", "order_id"), layer="silver"),
        # payments and ratings of orders that silver rejected are expected, so they are excused
        relationships((s, "payments", "order_id"), (s, "orders", "order_id"), layer="silver",
                      severity=WARNING,
                      ignore_if=f"EXISTS (SELECT 1 FROM {s}.rejected_rows r WHERE r.table_name"
                                " = 'orders' AND r.row_key = c.order_id)"),
        relationships((s, "ratings", "order_id"), (s, "orders", "order_id"), layer="silver",
                      severity=WARNING,
                      ignore_if=f"EXISTS (SELECT 1 FROM {s}.rejected_rows r WHERE r.table_name"
                                " = 'orders' AND r.row_key = c.order_id)"),
        value_range(s, "orders", "delivery_minutes", *VALID_DELIVERY_MINUTES, layer="silver"),
        # every order bronze received is either in silver or in the reject table, never lost
        sql_check("row_accounting:bronze.orders->silver", "silver", f"{s}.orders",
                  f"SELECT abs((SELECT count(DISTINCT order_id) FROM bronze.orders)"
                  f" - (SELECT count(*) FROM {s}.orders)"
                  f" - (SELECT count(*) FROM {s}.rejected_rows WHERE table_name = 'orders'"
                  " AND row_key LIKE 'ORD%'))",
                  description="bronze orders = silver orders + rejected orders"),
        sql_check("reject_rate:silver.orders", "silver", f"{s}.rejected_rows",
                  f"SELECT CASE WHEN total = 0 THEN 0 ELSE round(100.0 * bad / total, 2) END"
                  f" FROM (SELECT (SELECT count(*) FROM {s}.rejected_rows WHERE table_name ="
                  f" 'orders') bad, (SELECT count(DISTINCT order_id) FROM bronze.orders) total) x",
                  threshold=2.0, severity=WARNING,
                  description="no more than 2% of orders may be rejected"),
    ]
    return checks


def gold_checks(gold: str = "gold", silver: str = "silver") -> list[Check]:
    g, s = gold, silver
    checks = [not_null(g, "fact_orders", c, layer="gold")
              for c in ("order_id", "order_date_ist", "customer_sk", "restaurant_sk",
                        "partner_sk", "city_sk", "net_revenue")]
    checks += [
        unique(g, "fact_orders", ["order_id"], layer="gold"),
        unique(g, "fact_order_items", ["order_item_id"], layer="gold"),
        unique(g, "fact_refunds", ["refund_id"], layer="gold"),
        accepted_values(g, "fact_orders", "status", ORDER_STATUSES, layer="gold"),
        accepted_values(g, "fact_refunds", "reason_code", REFUND_REASONS, layer="gold",
                        severity=WARNING),
        relationships((g, "fact_orders", "customer_sk"), (g, "dim_customer", "customer_sk"),
                      layer="gold"),
        relationships((g, "fact_orders", "restaurant_sk"), (g, "dim_restaurant",
                      "restaurant_sk"), layer="gold"),
        relationships((g, "fact_order_items", "order_id"), (g, "fact_orders", "order_id"),
                      layer="gold"),
        relationships((g, "fact_refunds", "order_id"), (g, "fact_orders", "order_id"),
                      layer="gold"),
        sql_check("unknown_member:gold.fact_orders", "gold", f"{g}.fact_orders",
                  f"SELECT count(*) FROM {g}.fact_orders WHERE customer_sk = -1"
                  " OR restaurant_sk = -1 OR city_sk = -1",
                  description="orders that could not find their customer, restaurant or city"),
        value_range(g, "fact_orders", "delivery_minutes", *VALID_DELIVERY_MINUTES, layer="gold"),
        value_range(g, "fact_orders", "order_value", 0, 1_000_000, layer="gold"),
        sql_check("rule:discount_not_above_value", "gold", f"{g}.fact_orders",
                  f"SELECT count(*) FROM {g}.fact_orders WHERE discount > order_value",
                  description="a discount can never exceed the order value"),
        freshness(g, "fact_orders", "order_ts_utc", MAX_FRESHNESS_HOURS, layer="gold"),
        row_count_delta(g, "fact_orders", "order_date_ist", MAX_DAY_OVER_DAY_RATIO, layer="gold"),
        # --- reconciliation: gold must agree with silver, day by day ---
        sql_check("reconciliation:net_revenue_by_day", "gold", f"{g}.fact_orders",
                  f"SELECT count(*) FROM (SELECT order_date_ist d, sum(order_value - discount)"
                  f" v FROM {s}.orders WHERE status = 'delivered' GROUP BY 1) a FULL JOIN"
                  f" (SELECT order_date_ist d, sum(net_revenue) v FROM {g}.fact_orders GROUP BY 1)"
                  " b USING (d) WHERE abs(coalesce(a.v, 0) - coalesce(b.v, 0)) > 0.01",
                  description="days where gold revenue differs from silver revenue"),
        sql_check("reconciliation:order_count_by_day", "gold", f"{g}.fact_orders",
                  f"SELECT count(*) FROM (SELECT order_date_ist d, count(*) n FROM {s}.orders"
                  f" GROUP BY 1) a FULL JOIN (SELECT order_date_ist d, count(*) n FROM"
                  f" {g}.fact_orders GROUP BY 1) b USING (d)"
                  " WHERE coalesce(a.n, 0) <> coalesce(b.n, 0)",
                  description="days where gold order count differs from silver"),
        sql_check("reconciliation:daily_city_sales", "gold", f"{g}.daily_city_sales",
                  f"SELECT abs((SELECT coalesce(sum(net_revenue), 0) FROM {g}.fact_orders)"
                  f" - (SELECT coalesce(sum(net_revenue), 0) FROM {g}.daily_city_sales))",
                  description="the aggregate must equal the fact"),
        sql_check("reconciliation:items_vs_silver", "gold", f"{g}.fact_order_items",
                  f"SELECT abs((SELECT coalesce(sum(line_total), 0) FROM {g}.fact_order_items)"
                  f" - (SELECT coalesce(sum(line_total), 0) FROM {s}.order_items))",
                  description="item line totals must match silver"),
        # --- SCD2 integrity ---
        sql_check("scd2_integrity:overlapping_windows", "gold", f"{g}.dim_customer",
                  f"SELECT count(*) FROM (SELECT valid_from, lag(valid_to) OVER (PARTITION BY"
                  f" customer_id ORDER BY valid_from) prev_to FROM {g}.dim_customer) x"
                  " WHERE prev_to > valid_from",
                  description="a customer's versions must never overlap"),
        sql_check("scd2_integrity:gaps", "gold", f"{g}.dim_customer",
                  f"SELECT count(*) FROM (SELECT valid_from, lag(valid_to) OVER (PARTITION BY"
                  f" customer_id ORDER BY valid_from) prev_to FROM {g}.dim_customer) x"
                  " WHERE prev_to < valid_from",
                  description="a customer's versions must be back to back"),
        sql_check("scd2_integrity:one_current_row", "gold", f"{g}.dim_customer",
                  f"SELECT count(*) FROM (SELECT customer_id FROM {g}.dim_customer GROUP BY 1"
                  " HAVING count(*) FILTER (WHERE is_current) <> 1) x",
                  description="every customer has exactly one current version"),
        sql_check("scd2_integrity:current_matches_open_window", "gold", f"{g}.dim_customer",
                  f"SELECT count(*) FROM {g}.dim_customer WHERE is_current <> "
                  "(valid_to = DATE '9999-12-31')",
                  description="is_current must agree with an open validity window"),
    ]
    return checks


def build_checks(gold: str = "gold", silver: str = "silver") -> list[Check]:
    return silver_checks(silver) + gold_checks(gold, silver)
