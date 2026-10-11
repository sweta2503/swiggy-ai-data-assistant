"""The assistant as other code (the API, the UI, the command line, the evaluation) uses it."""
import logging
import uuid
from dataclasses import dataclass, field
from decimal import Decimal
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
from swiggy_ai.llm import LLMError, resolve_model
from swiggy_ai.ops.audit import AttemptRecord, RunRecord, ensure_audit_tables, record_run
from swiggy_ai.ops.cost import estimate_cost
from swiggy_ai.security.scope import AppUser

log = logging.getLogger("assistant")


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
    run_id: str = ""
    est_cost: Decimal | None = None  # US dollars; None when prices are not set
    audit_ok: bool = True  # False if the audit record could not be written

    @property
    def healed(self) -> bool:
        return any(a.status != "ok" for a in self.attempts) and self.status == "answered"


class Assistant:
    def __init__(self, runtime: Runtime | None = None, audit: bool = True):
        self.rt = runtime or Runtime.create()
        self.graph = build_graph(self.rt)
        self.audit = audit
        if audit:
            with self.rt.open_service() as conn:
                ensure_audit_tables(conn)

    def ask(self, question: str, user: AppUser, thread_id: str | None = None) -> Answer:
        """Answer one question, and write it to the audit trail (even refusals and failures)."""
        run_id = uuid.uuid4()
        thread = thread_id or uuid.uuid4().hex
        question = (question or "").strip()
        started = self.rt.clock()
        answer = self._answer(question, user, thread, started)
        answer.run_id = str(run_id)
        answer.est_cost = estimate_cost(answer.usage, self.rt.settings).usd
        if self.audit and question:
            answer.audit_ok = self._write_audit(run_id, question, user, answer)
        return answer

    def _answer(self, question: str, user: AppUser, thread: str, started: float) -> Answer:
        if not question:
            return Answer("failed", "Please type a question.", thread_id=thread)
        if len(question) > MAX_QUESTION_CHARS:
            return Answer("refused", f"That message is too long (over {MAX_QUESTION_CHARS} "
                                     "characters).", thread_id=thread)
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

    def _models(self) -> dict[str, str]:
        out = {}
        for tier in ("low", "medium", "high"):
            try:
                out[tier] = resolve_model(tier)
            except LLMError:
                out[tier] = ""
        return out

    def _write_audit(self, run_id: uuid.UUID, question: str, user: AppUser, a: Answer) -> bool:
        """Never let a broken audit table take the answer down with it, but say so loudly."""
        record = RunRecord(
            run_id=run_id, thread_id=a.thread_id, user_id=user.user_id, scope=user.allowed_cities,
            question=question, working_question=a.understood_as, route=a.route, status=a.status,
            healed=a.healed, steps=len(a.steps), final_sql=a.sql, latency_ms=a.latency_ms,
            tokens_in=a.usage.tokens_in, tokens_out=a.usage.tokens_out, est_cost=a.est_cost,
            usage_by_step=a.usage.by_step, models=self._models(),
            checks=[{"name": c.name, "severity": c.severity, "detail": c.detail,
                     "passed": c.passed} for c in a.checks],
            warnings=a.warnings, fault_injected=a.fault_injected,
            attempts=[AttemptRecord(i, t.item, t.source, t.sql, t.status, t.error_class,
                                    t.error_text, t.latency_ms)
                      for i, t in enumerate(a.attempts, start=1)])
        try:
            with self.rt.open_service() as conn:
                record_run(conn, record)
            return True
        except Exception as e:  # noqa: BLE001 - audit must not break answering
            log.error("AUDIT WRITE FAILED for run %s: %s", run_id, type(e).__name__)
            return False
