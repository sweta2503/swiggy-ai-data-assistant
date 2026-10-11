"""Schema agent: pick the slice of the semantic layer this question needs.

No model call. The choice is made by scoring (see semantic/context_builder.py), so it is cheap,
repeatable and testable. The node also resets everything that belongs to a single query attempt.
"""
from collections.abc import Callable

from swiggy_ai.agents.runtime import Runtime
from swiggy_ai.agents.state import AgentState
from swiggy_ai.semantic.context_builder import build_context


def question_for_item(state: AgentState) -> str:
    """The sub-question, plus a short recap of earlier steps it depends on."""
    cur = state.current
    text = cur.question
    done = {r.id: r for r in state.results if r.ok}
    recaps = []
    for dep in cur.depends_on:
        r = done.get(dep)
        if r and r.rows:
            shown = "; ".join(", ".join(f"{c}={v}" for c, v in zip(r.columns, row, strict=False))
                              for row in r.rows[:5])
            recaps.append(f"Step {dep} ({r.question}) returned: {shown}")
    if recaps:
        text += "\n\nResults of earlier steps you can rely on:\n" + "\n".join(recaps)
    return text


def make_schema_agent(rt: Runtime) -> Callable[[AgentState], dict]:
    def schema_agent(state: AgentState) -> dict:
        bundle = build_context(question_for_item(state), rt.settings.context_tokens, rt.semantic)
        return {
            "schema_context": bundle.text,
            "views": bundle.tables,
            # a clean slate for this sub-question
            "current_sql": "", "current_source": "writer", "item_ok": False, "assumptions": [],
            "hint": "", "cost_retry_used": False,
            "repairs_used": [], "last_error_class": "", "last_error_text": "", "columns": [],
            "rows": [], "truncated": False, "checks": [],
        }
    return schema_agent
