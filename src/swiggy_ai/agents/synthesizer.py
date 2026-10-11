"""Synthesizer: combine the results of several steps into one cited answer."""
from collections.abc import Callable

from swiggy_ai.agents.narrator import SHOW_ROWS, Narration, fmt, plain_answer
from swiggy_ai.agents.numbers import allowed_numbers, unsupported_numbers
from swiggy_ai.agents.runtime import Runtime
from swiggy_ai.agents.state import AgentState, StepResult


def step_text(step: StepResult) -> str:
    head = f"Step {step.id}: {step.question}"
    if not step.ok:
        return f"{head}\n  COULD NOT BE ANSWERED: {step.failure}"
    lines = [" | ".join(step.columns)]
    lines += [" | ".join(fmt(v) for v in row) for row in step.rows[:SHOW_ROWS]]
    if len(step.rows) > SHOW_ROWS:
        lines.append(f"... {len(step.rows) - SHOW_ROWS} more rows not shown")
    return f"{head}\n" + "\n".join(lines)


def plain_synthesis(state: AgentState) -> str:
    parts = []
    for s in state.results:
        parts.append(f"[{s.id}] " + (plain_answer(s) if s.ok else f"could not be answered "
                                                                  f"({s.failure})."))
    return "\n".join(parts)


def make_synthesizer(rt: Runtime) -> Callable[[AgentState], dict]:
    def synthesizer(state: AgentState) -> dict:
        ok = [s for s in state.results if s.ok]
        if not ok:
            why = "; ".join(f"step {s.id}: {s.failure}" for s in state.results)
            return {"status": "failed", "answer": f"I could not answer that: {why}."}

        user_input = "\n\n".join([f"Original question: {state.working_question}",
                                  *[step_text(s) for s in state.results]])
        allowed = allowed_numbers([], [r for s in ok for r in s.rows], state.working_question,
                                  extra=tuple(s.id for s in state.results))  # "[2]" citations
        usage, notes, extra = state.usage, [], ""
        text = None
        for _ in range(2):
            out, usage = rt.ask(state.model_copy(update={"usage": usage}), "synthesizer",
                                "synthesizer", user_input + extra, Narration, tier="medium")
            bad = unsupported_numbers(out.answer, allowed)
            if not bad:
                text = out.answer.strip()
                break
            extra = (f"\n\nYour previous answer used numbers that are not in the results: {bad}. "
                     "Rewrite it using only numbers from the results, exactly as shown.")
        if text is None:
            text = plain_synthesis(state)
            notes.append("The written summary could not be verified against the data, so the "
                         "plain figures are shown instead.")
        warnings = [*state.warnings, *notes, *rt.health_warnings()]
        for s in state.results:
            if not s.ok:
                warnings.append(f"Step {s.id} could not be answered: {s.failure}.")
        return {"answer": text, "status": "answered", "usage": usage, "warnings": warnings}
    return synthesizer
