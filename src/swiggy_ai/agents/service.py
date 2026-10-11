"""The assistant as other code (the API, the UI, the command line, the evaluation) uses it."""
import uuid
from dataclasses import dataclass, field
from typing import Any

from swiggy_ai.agents.graph import build_graph
from swiggy_ai.agents.router import MAX_QUESTION_CHARS
from swiggy_ai.agents.runtime import Runtime
from swiggy_ai.agents.state import (
    AgentState,
    Attempt,
    Check,
    Clarification,
    StepResult,
    Usage,
    fresh_turn,
)
from swiggy_ai.llm import LLMError
from swiggy_ai.security.scope import AppUser


@dataclass
class Answer:
    """Everything the caller needs to show an answer and explain how it was reached."""
    status: str  # answered | clarify | refused | failed
    answer: str
    sql: str = ""
    route: str = ""
    understood_as: str = ""
    steps: list[StepResult] = field(default_factory=list)
    attempts: list[Attempt] = field(default_factory=list)
    checks: list[Check] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    clarification: Clarification | None = None
    usage: Usage = field(default_factory=Usage)
    fault_injected: bool = False
    latency_ms: int = 0
    thread_id: str = ""

    @property
    def healed(self) -> bool:
        return any(a.status != "ok" for a in self.attempts) and self.status == "answered"


class Assistant:
    def __init__(self, runtime: Runtime | None = None):
        self.rt = runtime or Runtime.create()
        self.graph = build_graph(self.rt)

    def ask(self, question: str, user: AppUser, thread_id: str | None = None) -> Answer:
        thread = thread_id or uuid.uuid4().hex
        question = (question or "").strip()
        if not question:
            return Answer("failed", "Please type a question.", thread_id=thread)
        if len(question) > MAX_QUESTION_CHARS:
            return Answer("refused", f"That message is too long (over {MAX_QUESTION_CHARS} "
                                     "characters).", thread_id=thread)
        started = self.rt.clock()
        turn = fresh_turn(question, thread_id=thread, user_id=user.user_id,
                          allowed_cities=user.allowed_cities, started_at=started)
        try:
            raw: dict[str, Any] = self.graph.invoke(turn, {"configurable": {"thread_id": thread}})
        except LLMError as e:  # the model is unreachable or rejected the request
            return Answer("failed", f"The language model is not available right now: {e}",
                          thread_id=thread, latency_ms=int((self.rt.clock() - started) * 1000))
        state = AgentState(**raw)
        return Answer(
            status=state.status or "failed", answer=state.answer, sql=state.final_sql,
            route=state.route or "", understood_as=state.working_question, steps=state.results,
            attempts=state.attempts, checks=[c for s in state.results for c in s.checks],
            warnings=state.warnings, clarification=state.clarification, usage=state.usage,
            fault_injected=state.fault_injected, thread_id=thread,
            latency_ms=int((self.rt.clock() - started) * 1000))
