"""Narrator: explains the result in plain English, and proves its numbers came from the result."""
import logging
from collections.abc import Callable

from pydantic import BaseModel

from swiggy_ai.agents.numbers import allowed_numbers, unsupported_numbers
from swiggy_ai.agents.runtime import Runtime
from swiggy_ai.agents.state import AgentState, StepResult

log = logging.getLogger("agents.narrator")
SHOW_ROWS = 25


class Narration(BaseModel):
    answer: str


def fmt(value: object) -> str:
    """How a cell is shown to the model. The check below accepts exactly these figures."""
    if isinstance(value, float):
        return f"{value:.4f}" if 0 < abs(value) < 1 else f"{value:.2f}"
    return "" if value is None else str(value)


def table_text(step: StepResult) -> str:
    lines = [" | ".join(step.columns)]
    lines += [" | ".join(fmt(v) for v in row) for row in step.rows[:SHOW_ROWS]]
    if len(step.rows) > SHOW_ROWS:
        lines.append(f"... {len(step.rows) - SHOW_ROWS} more rows not shown")
    if step.truncated:
        lines.append("(the result was cut at the row limit)")
    return "\n".join(lines)


def plain_answer(step: StepResult) -> str:
    """A model-free answer. Used if the model keeps getting a number wrong, so we never publish
    a figure that is not in the data."""
    if not step.rows:
        return "No data matched this question."
    if len(step.rows) == 1 and len(step.columns) <= 4:
        return "; ".join(f"{c}: {fmt(v)}" for c, v in zip(step.columns, step.rows[0], strict=False))
    shown = "\n".join(", ".join(f"{c}: {fmt(v)}" for c, v in zip(step.columns, row, strict=False))
                      for row in step.rows[:10])
    return f"{len(step.rows)} rows. First rows:\n{shown}"


def narrate_text(rt: Runtime, state: AgentState, step: StepResult, user_input: str, prompt: str,
                 context: str) -> tuple[str, list[str], object]:
    """Ask the model, check its numbers, retry once, else fall back.
    Returns (text, notes, usage)."""
    allowed = allowed_numbers(step.columns, step.rows, context)
    usage, notes, extra = state.usage, [], ""
    for _ in range(2):
        out, usage = rt.ask(state.model_copy(update={"usage": usage}), prompt, prompt,
                            user_input + extra, Narration, tier="low")
        bad = unsupported_numbers(out.answer, allowed)
        if not bad:
            return out.answer.strip(), notes, usage
        log.warning("narration used numbers not in the result: %s", bad)
        extra = (f"\n\nYour previous answer used numbers that are not in the result: {bad}. "
                 "Rewrite it using only numbers from the result, exactly as shown.")
    notes.append("The written summary could not be verified against the data, so the plain "
                 "figures are shown instead.")
    return plain_answer(step), notes, usage


def make_narrator(rt: Runtime) -> Callable[[AgentState], dict]:
    def narrator(state: AgentState) -> dict:
        step = state.results[0]
        if not step.ok:
            status = "refused" if "safety guard" in step.failure else "failed"
            return {"status": status,
                    "answer": f"I could not answer that: {step.failure}.",
                    "warnings": [*state.warnings]}
        user_input = "\n".join([
            f"Question: {state.working_question or state.question}",
            f"Assumptions: {'; '.join(step.assumptions) or 'none'}",
            f"Result ({len(step.rows)} rows):", table_text(step)])
        text, notes, usage = narrate_text(rt, state, step, user_input, "narrator",
                                          state.working_question or state.question)
        return {"answer": text, "status": "answered", "usage": usage,
                "warnings": [*state.warnings, *notes, *rt.health_warnings()]}
    return narrator
