"""Cost check: ask Postgres how expensive the query would be BEFORE running it."""
from collections.abc import Callable
from typing import Any

import psycopg
import sqlglot
from sqlglot import exp

from swiggy_ai.agents.executor import classify_error, clean_error
from swiggy_ai.agents.runtime import Runtime
from swiggy_ai.agents.state import AgentState, Attempt
from swiggy_ai.security.sql_guard import referenced_views
from swiggy_ai.semantic.context_builder import TIME_WORDS, words

DATE_COLUMNS = {"order_date", "refund_date", "signup_date", "date"}
TIME_SERIES_VIEWS = {"orders", "order_items", "refunds", "daily_city_sales"}  # big and by date


def plan_cost(plan: dict[str, Any]) -> float:
    return float(plan["Total Cost"])


def lacks_date_filter(sql: str) -> bool:
    """True if the query reads a large dated view but never filters on a date column.

    Looked for in the SQL itself, not in the plan: when the period is relative to
    (SELECT today FROM analytics.data_as_of), Postgres cannot prune months at plan time, so the
    plan would look like a full scan even for a correct query."""
    tree = sqlglot.parse_one(sql, read="postgres")
    if not (referenced_views(sql) & TIME_SERIES_VIEWS):
        return False
    scopes = [*tree.find_all(exp.Where), *(j.args["on"] for j in tree.find_all(exp.Join)
                                           if j.args.get("on") is not None)]
    comparisons = (exp.GT, exp.GTE, exp.LT, exp.LTE, exp.EQ, exp.NEQ, exp.Between, exp.In)
    for scope in scopes:
        for cmp_ in scope.find_all(*comparisons):
            columns = list(cmp_.find_all(exp.Column))
            on_dates = [c for c in columns if c.name in DATE_COLUMNS]
            # a filter compares a date column with a value or expression; comparing it with
            # another column (a join key) selects nothing
            if on_dates and len(columns) == len(on_dates) and not _compares_two_columns(cmp_):
                return False
            if on_dates and not _compares_two_columns(cmp_):
                return False
    return True


def _compares_two_columns(cmp_: exp.Expression) -> bool:
    left, right = cmp_.args.get("this"), cmp_.args.get("expression")
    return isinstance(left, exp.Column) and isinstance(right, exp.Column)


def make_cost_check(rt: Runtime) -> Callable[[AgentState], dict]:
    def cost_check(state: AgentState) -> dict:
        item, no = state.current.id, len(state.item_attempts()) + 1
        conn = rt.open_assistant(state.allowed_cities)
        try:
            plan = conn.execute("EXPLAIN (FORMAT JSON) " + state.current_sql).fetchone()[0][0]
        except psycopg.Error as e:
            # The planner found a mistake (unknown column, bad syntax ...) before execution.
            attempt = Attempt(item=item, no=no, source=state.current_source,
                              sql=state.current_sql, status="error",
                              error_class=classify_error(e), error_text=clean_error(e))
            return {"attempts": [*state.attempts, attempt], "item_ok": False,
                    "last_error_class": attempt.error_class, "last_error_text": attempt.error_text}
        finally:
            conn.close()

        cost = plan_cost(plan["Plan"])
        problems = []
        if cost > rt.settings.max_plan_cost:
            problems.append(f"the estimated cost {cost:,.0f} is above the limit "
                            f"{rt.settings.max_plan_cost:,.0f}")
        asked_for_a_period = bool(words(state.current.question) & TIME_WORDS)
        if asked_for_a_period and lacks_date_filter(state.current_sql):
            problems.append("the question names a period but the query has no date filter, so "
                            "it reads every month of order data")
        if not problems:
            return {}

        text = "; ".join(problems)
        if not state.cost_retry_used:  # one chance to write a cheaper query
            attempt = Attempt(item=item, no=no, source=state.current_source,
                              sql=state.current_sql, status="rejected", error_class="cost",
                              error_text=text)
            return {"attempts": [*state.attempts, attempt], "cost_retry_used": True,
                    "hint": f"{text}. Filter to the period in the question and aggregate before "
                            "joining. Do NOT change what the question asks."}
        return {"warnings": [*state.warnings, f"This query is expensive: {text}."]}
    return cost_check
