"""Bookkeeping between steps: turn what happened into a StepResult and move to the next item."""
from collections.abc import Callable

from swiggy_ai.agents.state import AgentState, StepResult


def failure_text(state: AgentState) -> str:
    if state.last_error_class == "guard":
        return f"the query was blocked by the safety guard ({state.last_error_text})"
    labels = {"timeout": "the query took too long", "permission_denied": "access was denied"}
    why = labels.get(state.last_error_class, "the database rejected the query")
    return f"{why}: {state.last_error_text}" if state.last_error_text else why


def make_record_result() -> Callable[[AgentState], dict]:
    def record_result(state: AgentState) -> dict:
        cur = state.current
        ok = state.item_ok
        extra = len(state.item_attempts()) - 1
        step = StepResult(
            id=cur.id, question=cur.question, ok=ok,
            sql=state.current_sql if ok else "", columns=state.columns if ok else [],
            rows=state.rows if ok else [], truncated=state.truncated if ok else False,
            checks=state.checks, assumptions=state.assumptions if ok else [],
            healed=max(extra, 0) if ok else 0,
            failure="" if ok else failure_text(state))
        update = {"results": [*state.results, step], "item": state.item + 1}
        if ok:
            update["final_sql"] = state.current_sql
        return update
    return record_result
