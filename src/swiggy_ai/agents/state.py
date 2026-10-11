"""The state that travels through the agent graph.

Everything a node needs to know, and everything it learned, is in here, so a run can be replayed,
logged (phase 9) and shown step by step (phase 13). It is a Pydantic model on purpose: a node that
puts the wrong type in a field fails loudly instead of corrupting the run.
"""
import operator
from typing import Annotated, Any, Literal

from pydantic import BaseModel, Field

Route = Literal["simple", "multi_step", "ambiguous", "unsafe", "out_of_scope"]
Status = Literal["answered", "clarify", "refused", "failed"]
ErrorClass = Literal["syntax", "undefined_column", "undefined_table", "ambiguous_column",
                     "datatype_mismatch", "grouping_error", "timeout", "permission_denied",
                     "guard", "other"]
# What the healer may repair. Everything else is final.
HEALABLE: set[str] = {"syntax", "undefined_column", "undefined_table", "ambiguous_column",
                      "datatype_mismatch", "grouping_error"}


class SubQuestion(BaseModel):
    id: int
    question: str
    depends_on: list[int] = []


class Clarification(BaseModel):
    question: str
    options: list[str] = []


class Attempt(BaseModel):
    """One try at running one query."""
    item: int  # which sub-question (1-based id)
    no: int  # attempt number within that sub-question
    source: Literal["writer", "fault", "healer", "repair"]
    sql: str
    status: Literal["ok", "error", "rejected"]
    error_class: str = ""
    error_text: str = ""
    rows_preview: list[list[Any]] = []
    latency_ms: int = 0
    note: str = ""


class Check(BaseModel):
    name: str
    passed: bool
    severity: Literal["info", "warn", "repair"] = "info"
    detail: str = ""


class StepResult(BaseModel):
    id: int
    question: str
    ok: bool
    sql: str = ""
    columns: list[str] = []
    rows: list[list[Any]] = []
    truncated: bool = False
    checks: list[Check] = []
    assumptions: list[str] = []
    healed: int = 0  # how many extra attempts it took
    failure: str = ""  # why it did not work, in words


class Turn(BaseModel):
    """What the next question in the same conversation may refer back to."""
    question: str
    rewritten: str = ""
    route: str = ""
    sql: str = ""
    answer: str = ""


class Usage(BaseModel):
    calls: int = 0
    tokens_in: int = 0
    tokens_out: int = 0
    by_step: dict[str, dict[str, int]] = {}

    def plus(self, step: str, tokens_in: int, tokens_out: int) -> "Usage":
        by = {k: dict(v) for k, v in self.by_step.items()}
        row = by.setdefault(step, {"calls": 0, "tokens_in": 0, "tokens_out": 0})
        row["calls"] += 1
        row["tokens_in"] += tokens_in
        row["tokens_out"] += tokens_out
        return Usage(calls=self.calls + 1, tokens_in=self.tokens_in + tokens_in,
                     tokens_out=self.tokens_out + tokens_out, by_step=by)


class AgentState(BaseModel):
    # --- who is asking (set once per turn) ---
    question: str = ""
    thread_id: str = ""
    user_id: str = ""
    allowed_cities: str = ""  # the city scope: 'ALL' or 'Pune,Mumbai'
    history: Annotated[list[Turn], operator.add] = []  # kept across turns of a thread

    # --- routing and planning ---
    route: Route | None = None
    working_question: str = ""  # the question after follow-ups were resolved
    route_reason: str = ""
    clarification: Clarification | None = None
    refusal: str = ""
    plan: list[SubQuestion] = []
    item: int = 0  # index into plan of the sub-question being worked on

    # --- the query loop (reset for each sub-question) ---
    schema_context: str = ""
    views: list[str] = []
    current_sql: str = ""
    current_source: Literal["writer", "fault", "healer", "repair"] = "writer"
    item_ok: bool = False
    assumptions: list[str] = []
    hint: str = ""  # a repair instruction for the writer (from the cost check or result checker)
    cost_retry_used: bool = False
    repairs_used: list[str] = []  # names of result-check repairs already tried for this item
    last_error_class: str = ""
    last_error_text: str = ""
    columns: list[str] = []
    rows: list[list[Any]] = []
    truncated: bool = False
    checks: list[Check] = []
    attempts: list[Attempt] = []  # every attempt of the whole turn

    # --- outcome ---
    results: list[StepResult] = []
    final_sql: str = ""
    answer: str = ""
    status: Status | None = None
    warnings: list[str] = []
    usage: Usage = Field(default_factory=Usage)
    fault_injected: bool = False
    started_at: float = 0.0

    # ----------------------------------------------------------------------------------
    @property
    def current(self) -> SubQuestion | None:
        return self.plan[self.item] if 0 <= self.item < len(self.plan) else None

    def item_attempts(self) -> list[Attempt]:
        cur = self.current
        return [a for a in self.attempts if cur and a.item == cur.id]


def fresh_turn(question: str, *, thread_id: str, user_id: str, allowed_cities: str,
               started_at: float) -> dict[str, Any]:
    """The input for a new question. Every per-turn field is reset explicitly, because a
    checkpointed thread would otherwise remember the previous question's leftovers.
    (`history` is deliberately absent: it must survive.)"""
    blank = AgentState()
    reset = {k: getattr(blank, k) for k in AgentState.model_fields if k != "history"}
    reset.update(question=question, thread_id=thread_id, user_id=user_id,
                 allowed_cities=allowed_cities, started_at=started_at)
    return reset
