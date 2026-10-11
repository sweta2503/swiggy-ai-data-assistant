"""Planner: split a question that needs several queries into a few small, ordered ones."""
from collections.abc import Callable

from pydantic import BaseModel

from swiggy_ai.agents.runtime import Runtime
from swiggy_ai.agents.state import AgentState, SubQuestion

MAX_STEPS = 4


class PlanOut(BaseModel):
    sub_questions: list[SubQuestion]


def plan_problems(steps: list[SubQuestion]) -> list[str]:
    """Why a plan cannot be used (empty list = fine)."""
    if not steps:
        return ["the plan is empty"]
    problems = []
    if len(steps) > MAX_STEPS:
        problems.append(f"more than {MAX_STEPS} steps")
    ids = [s.id for s in steps]
    if ids != list(range(1, len(steps) + 1)):
        problems.append("ids must be 1, 2, 3 ... in order")
    for s in steps:
        if not s.question.strip():
            problems.append(f"step {s.id} has no question")
        bad = [d for d in s.depends_on if d >= s.id or d not in ids]
        if bad:
            problems.append(f"step {s.id} depends on {bad}, but a step may only depend on "
                            "earlier steps")
    return problems


def overview(rt: Runtime) -> str:
    views = "\n".join(f"- {t.name}: {t.description.strip()} (one row per {t.grain})"
                      for t in rt.semantic.catalog.tables if t.kind != "reference")
    metrics = ", ".join(m.name for m in rt.semantic.metrics.metrics)
    return f"Views:\n{views}\n\nMetrics: {metrics}"


def make_planner(rt: Runtime) -> Callable[[AgentState], dict]:
    def planner(state: AgentState) -> dict:
        usage, note = state.usage, ""
        for _ in range(2):  # one retry, told what was wrong
            out, usage = rt.ask(state.model_copy(update={"usage": usage}), "planner", "planner",
                                f"Question: {state.working_question}\n\n{overview(rt)}{note}",
                                PlanOut, tier="medium")
            problems = plan_problems(out.sub_questions)
            if not problems:
                steps = out.sub_questions
                if len(steps) == 1:  # nothing to split after all
                    return {"plan": steps, "item": 0, "route": "simple", "usage": usage}
                return {"plan": steps, "item": 0, "usage": usage}
            note = f"\n\nYour previous plan was rejected: {'; '.join(problems)}. Fix it."
        # Could not get a usable plan: answer the question as one query instead.
        return {"plan": [SubQuestion(id=1, question=state.working_question)], "item": 0,
                "route": "simple", "usage": usage,
                "warnings": [*state.warnings, "The question could not be broken into steps, so "
                             "it was answered as a single query."]}
    return planner
