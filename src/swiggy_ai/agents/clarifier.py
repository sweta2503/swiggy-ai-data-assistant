"""Clarifier: when a question is missing something, ask ONE short question and stop."""
from collections.abc import Callable

from pydantic import BaseModel

from swiggy_ai.agents.runtime import Runtime
from swiggy_ai.agents.state import AgentState, Clarification


class ClarifyOut(BaseModel):
    question: str
    options: list[str] = []


def make_clarifier(rt: Runtime) -> Callable[[AgentState], dict]:
    def clarifier(state: AgentState) -> dict:
        out, usage = rt.ask(state, "clarifier", "clarifier",
                            f"Question that needs more information: {state.working_question}",
                            ClarifyOut, tier="low")
        options = [o.strip() for o in out.options if o.strip()][:3]
        text = out.question.strip()
        if options:
            text += " For example: " + ", ".join(options) + "."
        return {"clarification": Clarification(question=out.question.strip(), options=options),
                "answer": text, "status": "clarify", "usage": usage}
    return clarifier
