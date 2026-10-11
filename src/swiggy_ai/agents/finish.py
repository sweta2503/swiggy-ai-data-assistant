"""The nodes that end a run: refusal, and the bookkeeping that remembers the turn."""
from collections.abc import Callable

from swiggy_ai.agents.state import AgentState, SubQuestion, Turn


def plan_simple(state: AgentState) -> dict:
    """A question that needs one query becomes a one-step plan."""
    q = state.working_question or state.question
    return {"plan": [SubQuestion(id=1, question=q)], "item": 0}


def refusal(state: AgentState) -> dict:
    return {"answer": state.refusal or "I cannot help with that.", "status": "refused"}


def make_finalize() -> Callable[[AgentState], dict]:
    def finalize(state: AgentState) -> dict:
        """Remember this turn so a follow-up ("and in Pune?") can refer back to it."""
        turn = Turn(question=state.question, rewritten=state.working_question,
                    route=state.route or "", sql=state.final_sql, answer=state.answer[:500])
        return {"history": [turn]}
    return finalize
