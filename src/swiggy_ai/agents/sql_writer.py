"""SQL writer: turns the question plus the semantic context into one SELECT query."""
import logging
from collections.abc import Callable

from pydantic import BaseModel

from swiggy_ai.agents.faults import inject_fault
from swiggy_ai.agents.runtime import Runtime
from swiggy_ai.agents.schema_agent import question_for_item
from swiggy_ai.agents.state import AgentState

log = logging.getLogger("agents.sql_writer")


class SQLDraft(BaseModel):
    sql: str
    assumptions: list[str] = []


def make_sql_writer(rt: Runtime) -> Callable[[AgentState], dict]:
    def sql_writer(state: AgentState) -> dict:
        parts = [f"Question: {question_for_item(state)}", "", state.schema_context]
        if state.hint:  # a second try: show the model what was wrong
            parts += ["", "Your previous query was not good enough. Write a corrected one.",
                      f"Previous query:\n{state.current_sql}", f"Problem: {state.hint}"]
        draft, usage = rt.ask(state, "sql_writer", "sql_writer", "\n".join(parts), SQLDraft,
                              tier="medium")
        sql = draft.sql.strip().rstrip(";").strip()
        first_try = not state.item_attempts()
        update = {"current_sql": sql, "assumptions": draft.assumptions, "hint": "",
                  "last_error_class": "", "last_error_text": "", "usage": usage,
                  "current_source": "writer" if first_try else "repair"}

        # Demo only: break the first query on purpose so the healer has something to fix.
        if rt.settings.demo_fault_injection and first_try and not state.fault_injected:
            broken, fault = inject_fault(sql, rt.settings.demo_fault_name)
            log.warning("fault injected: %s", fault["name"])
            note = f"Fault injected for the demo: {fault['why']}."
            update.update(current_sql=broken, current_source="fault", fault_injected=True,
                          warnings=[*state.warnings, note])
        return update
    return sql_writer
