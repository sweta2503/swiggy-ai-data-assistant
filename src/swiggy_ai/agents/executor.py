"""Executor: run the approved query as the read-only, city-scoped assistant role."""
import time
from collections.abc import Callable

import psycopg

from swiggy_ai.agents.runtime import Runtime, jsonable
from swiggy_ai.agents.state import AgentState, Attempt

# Postgres error code -> the error classes the healer understands.
# 42883 ("function does not exist, you might need explicit casts") is grouped with datatype
# problems because Postgres itself words it that way; it is how SQL Server functions such as
# DATEADD show up.
SQLSTATE_CLASS = {
    "42601": "syntax", "42703": "undefined_column", "42P01": "undefined_table",
    "42702": "ambiguous_column", "42804": "datatype_mismatch", "42883": "datatype_mismatch",
    "22P02": "datatype_mismatch", "22007": "datatype_mismatch", "22008": "datatype_mismatch",
    "42846": "datatype_mismatch", "42803": "grouping_error", "57014": "timeout",
    "42501": "permission_denied",
}


def classify_error(e: psycopg.Error) -> str:
    return SQLSTATE_CLASS.get(e.sqlstate or "", "other")


def clean_error(e: psycopg.Error) -> str:
    """The database's own words, without the noisy position pointer."""
    primary = e.diag.message_primary or str(e)
    hint = f" Hint: {e.diag.message_hint}" if e.diag.message_hint else ""
    return (primary + hint)[:400]


def make_executor(rt: Runtime) -> Callable[[AgentState], dict]:
    def executor(state: AgentState) -> dict:
        if not state.allowed_cities.strip():
            raise PermissionError("no city scope was provided; refusing to run a query")
        started = time.time()
        item, no = state.current.id, len(state.item_attempts()) + 1
        conn = rt.open_assistant(state.allowed_cities)
        try:
            conn.execute(f"SET statement_timeout = {int(rt.settings.query_timeout_ms)}")
            cur = conn.execute(state.current_sql)
            columns = [d.name for d in cur.description]
            fetched = cur.fetchmany(rt.settings.max_rows)
        except psycopg.Error as e:
            attempt = Attempt(item=item, no=no, source=state.current_source, sql=state.current_sql,
                              status="error", error_class=classify_error(e),
                              error_text=clean_error(e),
                              latency_ms=int((time.time() - started) * 1000))
            return {"attempts": [*state.attempts, attempt], "item_ok": False,
                    "last_error_class": attempt.error_class, "last_error_text": attempt.error_text}
        finally:
            conn.close()

        # the guard caps every query at max_rows, so reaching the cap means it may have been cut
        truncated = len(fetched) >= rt.settings.max_rows
        rows = [[jsonable(v) for v in row] for row in fetched]
        attempt = Attempt(item=item, no=no, source=state.current_source, sql=state.current_sql,
                          status="ok", rows_preview=rows[:5],
                          latency_ms=int((time.time() - started) * 1000))
        return {"attempts": [*state.attempts, attempt], "item_ok": True, "columns": columns,
                "rows": rows, "truncated": truncated, "last_error_class": "",
                "last_error_text": ""}
    return executor
