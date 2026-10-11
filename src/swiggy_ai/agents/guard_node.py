"""Guard node: runs the deterministic SQL guard. A refusal is final: it is never repaired."""
from collections.abc import Callable

from swiggy_ai.agents.runtime import Runtime
from swiggy_ai.agents.state import AgentState, Attempt
from swiggy_ai.security.sql_guard import check_sql

REPAIRABLE_CODES = {"parse_error", "empty"}


def make_guard(rt: Runtime) -> Callable[[AgentState], dict]:
    def guard_node(state: AgentState) -> dict:
        result = check_sql(state.current_sql, rt.allowed_views, rt.settings.max_rows)
        if result.ok:
            return {"current_sql": result.sql}
        # A query that is merely broken (cannot be parsed, or is empty) is an ordinary mistake and
        # may be repaired. Everything else the guard refuses is a boundary and is final.
        error_class = "syntax" if result.code in REPAIRABLE_CODES else "guard"
        attempt = Attempt(item=state.current.id, no=len(state.item_attempts()) + 1,
                          source=state.current_source, sql=state.current_sql, status="rejected",
                          error_class=error_class, error_text=f"{result.code}: {result.reason}")
        return {"attempts": [*state.attempts, attempt], "item_ok": False,
                "last_error_class": error_class, "last_error_text": result.reason}
    return guard_node
