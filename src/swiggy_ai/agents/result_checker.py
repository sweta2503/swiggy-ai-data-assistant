"""Result checker: catches answers that RAN fine but are probably wrong.

An error message is easy: the database tells you. The dangerous failures are silent: a join that
repeats every order once per item line and inflates revenue three-fold, "last month" computed from
the wall clock instead of the data, a rate of 140%. The checks below are deterministic code.
A model read is available but optional and can only add warnings.

Two kinds of finding:
  repair  the query is probably wrong: send it back to the writer with an explanation
  warn    the result may surprise the reader: say so next to the answer
"""
import difflib
import re
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

import psycopg
import sqlglot
from pydantic import BaseModel
from sqlglot import exp

from swiggy_ai.agents.executor import clean_error
from swiggy_ai.agents.runtime import Runtime
from swiggy_ai.agents.state import AgentState, Check
from swiggy_ai.security.sql_guard import check_sql
from swiggy_ai.semantic.loader import Semantic

ORDER_MEASURES = {"order_value", "discount", "delivery_fee", "taxes", "net_revenue"}
PERIODS = [  # most specific first
    ("last_7_days", r"\b(last|past|previous) (7|seven) days\b"),
    ("last_week", r"\b(last|previous|prior) week\b"),
    ("this_week", r"\b(this|current) week\b"),
    ("last_month", r"\b(last|previous|prior) month\b"),
    ("this_month", r"\b(this|current) month\b|\bmonth[- ]to[- ]date\b|\bmtd\b"),
    ("yesterday", r"\byesterday\b"),
]
CLOCK_NODES = tuple(getattr(exp, n) for n in ("CurrentDate", "CurrentTimestamp", "CurrentTime",
                                              "CurrentDatetime", "Localtimestamp", "Localtime")
                    if hasattr(exp, n))


class Sanity(BaseModel):
    looks_wrong: bool
    concern: str = ""


@dataclass
class Finding:
    name: str
    severity: str  # repair | warn
    detail: str
    hint: str = ""  # what to tell the writer, for repairs


# ---------------------------------------------------------------- pure checks ---------------------
def detect_period(question: str) -> str | None:
    q = question.lower()
    return next((name for name, pattern in PERIODS if re.search(pattern, q)), None)


def check_clock_and_period(question: str, sql: str, sem: Semantic) -> list[Finding]:
    findings = []
    tree = sqlglot.parse_one(sql, read="postgres")
    uses_clock = any(tree.find_all(*CLOCK_NODES)) or any(
        isinstance(f, exp.Anonymous) and f.name.lower() in ("now", "clock_timestamp")
        for f in tree.find_all(exp.Func))
    if uses_clock:
        findings.append(Finding(
            "uses_real_clock", "repair", "the query reads today's date from the clock",
            "It uses the wall clock (CURRENT_DATE / NOW()). The data stops on a fixed day, so "
            "use (SELECT today FROM analytics.data_as_of) instead, as the conventions say."))
    period = detect_period(question)
    if period and "data_as_of" not in sql.lower():
        template = sem.metrics.conventions.period_templates.get(period, "")
        today = "(SELECT today FROM analytics.data_as_of)"
        example = template.replace("<col>", "order_date").replace(" T", f" {today}") \
            .replace("(T", f"({today}").replace("T)", f"{today})").replace("= T", f"= {today}")
        if not uses_clock:
            findings.append(Finding(
                "period_not_anchored", "repair",
                f"the question says '{period.replace('_', ' ')}' but the query does not use "
                "data_as_of",
                f"The question refers to '{period.replace('_', ' ')}' but the query does not "
                "measure it from analytics.data_as_of. Filter the date column like this: "
                f"{example}"))
    return findings


def check_literals(sql: str, sem: Semantic) -> list[Finding]:
    """A filter such as item_category = 'Beverage' returns nothing when the column holds
    'Beverages'. For columns whose complete list of values is in the catalog, check the spelling."""
    allowed: dict[str, list[str]] = {}
    for table in sem.catalog.tables:
        for col in table.columns:
            if col.allowed_values:
                allowed.setdefault(col.name, col.allowed_values)
    tree = sqlglot.parse_one(sql, read="postgres")
    seen: list[tuple[str, str]] = []
    for node in tree.find_all(exp.EQ, exp.NEQ, exp.In):
        if isinstance(node, exp.In):
            sides = [(node.this, e) for e in node.expressions]
        else:
            sides = [(node.left, node.right), (node.right, node.left)]
        for column, literal in sides:
            if (isinstance(column, exp.Column) and column.name in allowed
                    and isinstance(literal, exp.Literal) and literal.is_string
                    and literal.name not in allowed[column.name]
                    and (column.name, literal.name) not in seen):
                seen.append((column.name, literal.name))
    findings = []
    for name, value in seen:
        close = difflib.get_close_matches(value, allowed[name], n=1, cutoff=0.6)
        findings.append(Finding(
            "unknown_value", "repair", f"'{value}' is not a value of {name}",
            f"'{value}' is not a value that {name} can hold, so the filter matches nothing. "
            f"The values are exactly: {', '.join(allowed[name])}."
            + (f" Did you mean '{close[0]}'?" if close else "")))
    return findings


ASKS_ABOUT_ORDERS = re.compile(r"\borders?\b", re.IGNORECASE)
ASKS_ABOUT_LINES = re.compile(r"\b(items?|dish(?:es)?|lines?|units?|quantity|qty|sold)\b",
                              re.IGNORECASE)


def check_count_grain(question: str, sql: str) -> list[Finding]:
    """order_items has one row per ITEM LINE. Counting its rows answers "how many item lines", and a
    question about ORDERS needs COUNT(DISTINCT order_id). Nothing errors and the number looks fine
    (an order with three dishes counts three times), so it has to be caught by reading the SQL.
    Joins with orders are the fan-out check's job; this one is for a query on order_items alone."""
    if not ASKS_ABOUT_ORDERS.search(question) or ASKS_ABOUT_LINES.search(question):
        return []
    tree = sqlglot.parse_one(sql, read="postgres")
    tables = {t.name for t in tree.find_all(exp.Table)}
    if "order_items" not in tables or "orders" in tables:
        return []
    plain_counts = [c for c in tree.find_all(exp.Count) if not isinstance(c.this, exp.Distinct)]
    if not plain_counts:
        return []
    return [Finding(
        "item_lines_counted_as_orders", "repair",
        "it counts rows of order_items (one per item line), but the question is about orders",
        "analytics.order_items has one row per ITEM LINE, so COUNT(*) counts item lines, not "
        "orders: an order with three dishes is counted three times. To count orders use "
        "COUNT(DISTINCT order_id).")]


def check_values(columns: list[str], rows: list[list[Any]], truncated: bool,
                 max_rows: int) -> list[Finding]:
    findings = []
    if not rows:
        findings.append(Finding("empty_result", "warn", "the query returned no rows"))
        return findings
    if all(cell is None for row in rows for cell in row):
        findings.append(Finding("all_null_result", "warn", "every value in the result is empty"))
    if truncated:
        findings.append(Finding("row_cap", "warn", f"the result was cut at {max_rows} rows"))
    for i, name in enumerate(columns):
        values = [r[i] for r in rows
                  if isinstance(r[i], int | float) and not isinstance(r[i], bool)]
        low = name.lower()
        if any(w in low for w in ("revenue", "aov", "gross_order_value")) and any(
                v < 0 for v in values):
            findings.append(Finding("negative_revenue", "warn", f"'{name}' has a negative value"))
        if low.endswith(("_rate", "_ratio")) and any(v < 0 or v > 1 for v in values):
            findings.append(Finding(
                "rate_out_of_range", "warn",
                f"'{name}' should be a fraction between 0 and 1 but has {max(values):g}"))
    return findings


# ---- join fan-out ---------------------------------------------------------------
@dataclass
class FanOutCandidate:
    column_index: int | None  # which output column holds the suspect aggregate
    aggregate: exp.Expression
    kind: str  # sum | count | avg
    orders_alias: str
    control_sql: str | None  # a trusted query for what the number should be (None = cannot build)


def _aliases(select: exp.Select) -> dict[str, str]:
    """alias (or name) -> view name, for the tables joined in this SELECT."""
    out = {}
    for t in select.find_all(exp.Table):
        if t.name:
            out[(t.alias or t.name).lower()] = t.name.lower()
    return out


def find_fan_out(sql: str, sem: Semantic) -> FanOutCandidate | None:
    """Look for: orders joined to order_items, with an order-level figure summed or counted."""
    tree = sqlglot.parse_one(sql, read="postgres")
    select = tree if isinstance(tree, exp.Select) else None
    if select is None:
        return None
    aliases = _aliases(select)
    order_alias = next((a for a, v in aliases.items() if v == "orders"), None)
    if order_alias is None or "order_items" not in aliases.values():
        return None
    orders_cols = {c.name for c in sem.catalog.table("analytics.orders").columns}
    item_cols = {c.name for c in sem.catalog.table("analytics.order_items").columns}

    def is_order_level(col: exp.Column) -> bool:
        if col.table:
            return col.table.lower() == order_alias
        return col.name in orders_cols and col.name not in item_cols

    for idx, proj in enumerate(select.expressions):
        node = proj.this if isinstance(proj, exp.Alias) else proj
        for agg in node.find_all(exp.AggFunc):
            if isinstance(agg, exp.Min | exp.Max) or isinstance(agg.this, exp.Distinct):
                continue
            cols = list(agg.find_all(exp.Column))
            distorted = (
                isinstance(agg, exp.Count) and (agg.find(exp.Star) is not None
                                                or any(is_order_level(c) for c in cols))
            ) or (isinstance(agg, exp.Sum | exp.Avg)
                  and any(c.name in ORDER_MEASURES and is_order_level(c) for c in cols))
            if not distorted:
                continue
            kind = "count" if isinstance(agg, exp.Count) else "avg" if isinstance(agg, exp.Avg) \
                else "sum"
            has_star = any(isinstance(p, exp.Star) for p in select.expressions)
            return FanOutCandidate(None if has_star else idx, agg, kind, order_alias,
                                   _control_sql(select, agg, order_alias, is_order_level, kind))
    return None


def _control_sql(select: exp.Select, agg: exp.Expression, order_alias: str,
                 is_order_level: Callable[[exp.Column], bool], kind: str) -> str | None:
    """The same figure from analytics.orders alone, with only the filters that apply to orders.
    Returns None when the query filters on something orders cannot answer (so no fair control
    exists) or when the aggregate cannot be compared (an average)."""
    if kind == "avg":
        return None
    for join in select.args.get("joins") or []:
        on = join.args.get("on")
        if on is not None and not _is_plain_key_join(on):
            return None  # the join itself filters (e.g. on an item category)
    where = select.args.get("where")
    kept = []
    if where is not None:
        parts = list(where.this.flatten()) if isinstance(where.this, exp.And) else [where.this]
        for part in parts:
            cols = list(part.find_all(exp.Column))
            if part.find(exp.Select) or not all(is_order_level(c) for c in cols):
                return None  # a filter on items/other tables: orders alone cannot reproduce it
            kept.append(part.sql(dialect="postgres"))
    agg_sql = agg.sql(dialect="postgres")
    where_sql = f" WHERE {' AND '.join(kept)}" if kept else ""
    return f"SELECT {agg_sql} FROM analytics.orders AS {order_alias}{where_sql}"


def _is_plain_key_join(on: exp.Expression) -> bool:
    """True for `a.order_id = b.order_id` and nothing more."""
    return isinstance(on, exp.EQ) and all(isinstance(side, exp.Column) and side.name == "order_id"
                                          for side in (on.this, on.expression))


def check_fan_out(rt: Runtime, state: AgentState, sql: str) -> Finding | None:
    cand = find_fan_out(sql, rt.semantic)
    if cand is None:
        return None
    advice = ("The query joins analytics.orders to analytics.order_items, so every order is "
              "repeated once per item line, and the order-level figure being summed or counted "
              "comes out too large. Answer from analytics.orders alone, or aggregate "
              "analytics.order_items separately first (use line_total), or use COUNT(DISTINCT "
              "order_id). Follow the 'Do not' notes of the views.")
    # A numeric check is only fair when the result holds every group and nothing was filtered
    # that orders cannot reproduce.
    user_limit = re.search(r"\bLIMIT (\d+)\b", sql, re.I)
    can_compare = (cand.control_sql is not None and cand.column_index is not None
                   and not state.truncated
                   and not (user_limit and int(user_limit.group(1)) != rt.settings.max_rows))
    if not can_compare:
        return Finding("fan_out_suspected", "repair",
                       "orders are joined to order items and an order-level figure is "
                       "aggregated (could not be verified against a control total)", advice)
    guarded = check_sql(cand.control_sql, rt.allowed_views, rt.settings.max_rows)
    conn = rt.open_assistant(state.allowed_cities)
    try:
        control = conn.execute(guarded.sql).fetchone()[0]
    except psycopg.Error as e:
        return Finding("fan_out_suspected", "repair",
                       f"could not run the control query ({clean_error(e)})", advice)
    finally:
        conn.close()
    actual = sum(r[cand.column_index] for r in state.rows
                 if isinstance(r[cand.column_index], int | float))
    control = float(control or 0)
    if abs(actual - control) <= 0.01 + 1e-6 * abs(control):
        return None  # the join did not change the total: fine
    ratio = actual / control if control else float("inf")
    return Finding("fan_out_suspected", "repair",
                   f"the figure adds up to {actual:,.2f} but analytics.orders alone gives "
                   f"{control:,.2f} ({ratio:.2f}x)",
                   f"{advice} Your result totals {actual:,.2f}; the correct total for the same "
                   f"filters is {control:,.2f}.")


# ---- the node -------------------------------------------------------------------
def make_result_checker(rt: Runtime) -> Callable[[AgentState], dict]:
    def result_checker(state: AgentState) -> dict:
        sql, cur = state.current_sql, state.current
        findings: list[Finding] = []
        findings += check_values(state.columns, state.rows, state.truncated, rt.settings.max_rows)
        findings += check_clock_and_period(cur.question, sql, rt.semantic)
        findings += check_literals(sql, rt.semantic)
        findings += check_count_grain(cur.question, sql)
        fan_out = check_fan_out(rt, state, sql)
        if fan_out:
            findings.append(fan_out)

        usage = state.usage
        if rt.settings.llm_sanity_check and state.rows:
            sample = "\n".join(" | ".join(str(v) for v in r) for r in state.rows[:5])
            read, usage = rt.ask(
                state, "result_sanity", "result_sanity",
                f"Question: {cur.question}\n\nSQL:\n{sql}\n\nColumns: {', '.join(state.columns)}\n"
                f"First rows:\n{sample}", Sanity, tier="low")
            if read.looks_wrong:  # a model's opinion is only ever a warning
                findings.append(Finding("model_sanity_read", "warn", read.concern))

        checks = [Check(name=f.name, passed=False, severity=f.severity, detail=f.detail)
                  for f in findings]
        warnings = list(state.warnings)
        heals_used = sum(1 for a in state.item_attempts() if a.source in ("healer", "repair"))
        repairs = [f for f in findings
                   if f.severity == "repair" and f.name not in state.repairs_used]
        if repairs and heals_used < rt.settings.max_heal_attempts:
            # Not good enough: record that, and send it back to the writer with the reasons.
            attempts = list(state.attempts)
            last = max(i for i, a in enumerate(attempts) if a.item == cur.id and a.status == "ok")
            attempts[last] = attempts[last].model_copy(update={
                "status": "rejected", "error_class": "result_check",
                "error_text": "; ".join(f.name for f in repairs)})
            return {"attempts": attempts, "checks": checks, "usage": usage,
                    "hint": " ".join(f.hint for f in repairs),
                    "repairs_used": [*state.repairs_used, *(f.name for f in repairs)],
                    "item_ok": False, "last_error_class": "", "last_error_text": ""}

        for f in findings:  # nothing more to try: tell the reader what is uncertain
            if f.severity == "repair":
                warnings.append(f"Please treat this result with care: {f.detail}.")
            elif f.name != "empty_result":
                warnings.append(f"{f.detail[0].upper()}{f.detail[1:]}.")
        return {"checks": checks, "usage": usage, "warnings": warnings}
    return result_checker

