"""Healer: repair a query that failed with an ordinary SQL mistake.

Only the six mistake classes in HEALABLE are repaired, plus ONE attempt for a timeout. Guard
refusals and permission errors are boundaries, not bugs, so they are never sent here. The healer
stops when it hits MAX_HEAL_ATTEMPTS or when it proposes a query it has already tried.
"""
import hashlib
from collections.abc import Callable

from pydantic import BaseModel

from swiggy_ai.agents.runtime import Runtime
from swiggy_ai.agents.state import HEALABLE, AgentState
from swiggy_ai.security.sql_guard import check_sql, referenced_views
from swiggy_ai.semantic.context_builder import render_table


class Repair(BaseModel):
    sql: str
    changed: str = ""


def fingerprint(rt: Runtime, sql: str) -> str:
    """The same query written twice looks the same after the guard has normalised it."""
    g = check_sql(sql, rt.allowed_views, rt.settings.max_rows)
    return hashlib.sha256((g.sql if g.ok else " ".join(sql.lower().split())).encode()).hexdigest()


def can_heal(rt: Runtime, state: AgentState) -> bool:
    """Is another repair allowed for this sub-question, given what has already been tried?"""
    attempts = state.item_attempts()
    cls = state.last_error_class
    heals = sum(1 for a in attempts if a.source == "healer")
    if heals >= rt.settings.max_heal_attempts:
        return False
    if cls == "timeout":  # one go: if the rewrite is still too slow, more tries will not help
        return sum(1 for a in attempts if a.error_class == "timeout") <= 1
    return cls in HEALABLE


def catalog_excerpt(rt: Runtime, state: AgentState) -> str:
    """The documentation for the views the failed query used, so column names can be checked."""
    try:
        names = referenced_views(state.current_sql)
    except Exception:  # noqa: BLE001 - the query may be too broken to parse
        names = set()
    tables = [t for t in rt.semantic.catalog.tables if t.name.split(".", 1)[1] in names]
    if not tables:
        tables = [t for t in rt.semantic.catalog.tables if t.name in state.views]
    return "\n\n".join(render_table(t, 2) for t in tables)


def make_healer(rt: Runtime) -> Callable[[AgentState], dict]:
    def healer(state: AgentState) -> dict:
        tried = {fingerprint(rt, a.sql) for a in state.item_attempts()}
        timeout_note = ("\nThis was a timeout. Do less work; do not change what is asked."
                        if state.last_error_class == "timeout" else "")
        user_input = "\n".join([
            f"Question: {state.current.question}",
            f"\nFailed query:\n{state.current_sql}",
            f"\nError class: {state.last_error_class}",
            f"Database error: {state.last_error_text}{timeout_note}",
            "\n## Allowed views and columns", catalog_excerpt(rt, state)])
        repair, usage = rt.ask(state, "healer", "healer", user_input, Repair, tier="medium")
        sql = repair.sql.strip().rstrip(";").strip()

        if fingerprint(rt, sql) in tried:  # it proposed something it already tried: stop
            return {"usage": usage, "item_ok": False, "last_error_class": "other",
                    "last_error_text": "The repair produced a query that had already failed, "
                                       "so I stopped trying."}
        return {"current_sql": sql, "current_source": "healer", "usage": usage,
                "assumptions": [*state.assumptions, repair.changed] if repair.changed
                else state.assumptions,
                "last_error_class": "", "last_error_text": ""}
    return healer
