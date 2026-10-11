"""The assistant as other code (the API, the UI, the command line, the evaluation) uses it."""
import logging
import uuid
from dataclasses import dataclass, field, replace
from decimal import Decimal
from typing import Any

from pydantic import TypeAdapter

from swiggy_ai.agents.graph import build_graph
from swiggy_ai.agents.router import MAX_QUESTION_CHARS
from swiggy_ai.agents.runtime import Runtime
from swiggy_ai.agents.state import (
    AgentState,
    Attempt,
    Check,
    Clarification,
    StepResult,
    Turn,
    Usage,
    fresh_turn,
)
from swiggy_ai.llm import LLMError, resolve_model
from swiggy_ai.logging_setup import scrub
from swiggy_ai.ops import cache as answer_cache
from swiggy_ai.ops.audit import AttemptRecord, RunRecord, ensure_audit_tables, record_run
from swiggy_ai.ops.cost import estimate_cost
from swiggy_ai.security.scope import AppUser
from swiggy_ai.semantic.loader import load_semantic

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
    cache_hit: bool = False  # served from ops.answer_cache: no model was called
    cached_at: str = ""  # when the cached answer was worked out

    @property
    def healed(self) -> bool:
        return any(a.status != "ok" for a in self.attempts) and self.status == "answered"


ANSWER = TypeAdapter(Answer)
CACHEABLE_ROUTES = {"simple", "multi_step"}


@dataclass
class CachePlan:
    key: str
    question_norm: str
    data_version: int
    scope: str


class Assistant:
    """`cache=True` serves repeated first questions from ops.answer_cache. It is off by default so
    that tests and the evaluation always measure the real thing; the interfaces turn it on."""

    def __init__(self, runtime: Runtime | None = None, audit: bool = True, cache: bool = False,
                 reload_catalog: bool = False):
        self.rt = runtime or Runtime.create()
        self.graph = build_graph(self.rt)
        self.audit = audit
        self.cache = cache
        self.reload_catalog = reload_catalog
        self._seen_version: int | None = None
        if audit or cache or reload_catalog:
            with self.rt.open_service() as conn:
                if audit:
                    ensure_audit_tables(conn)
                if cache or reload_catalog:
                    answer_cache.ensure_cache_tables(conn)

    @classmethod
    def for_app(cls, runtime: Runtime | None = None) -> "Assistant":
        """The assistant as the API, the UI and the command line run it: audited, cached when
        ANSWER_CACHE is on, and reloading the catalog whenever `make daily` publishes new data."""
        rt = runtime or Runtime.create()
        return cls(rt, audit=True, cache=rt.settings.answer_cache, reload_catalog=True)

    def _reload_if_new_data(self) -> None:
        """A long-running process keeps the catalog it loaded at start. When the data version moves
        (a daily run published, and may have rewritten catalog.yml), load it again."""
        if not self.reload_catalog:
            return
        try:
            with self.rt.open_service() as conn:
                version = answer_cache.get_data_version(conn)
            if self._seen_version is not None and version != self._seen_version:
                self.rt.semantic = load_semantic()
                log.info("data version %s -> %s: catalog reloaded", self._seen_version, version)
            self._seen_version = version
        except Exception as e:  # noqa: BLE001 - keep the catalog we have rather than fail
            log.error("could not reload the catalog (%s); keeping the current one",
                      type(e).__name__)

    def ask(self, question: str, user: AppUser, thread_id: str | None = None,
            fresh: bool = False) -> Answer:
        """Answer one question, and write it to the audit trail (even refusals and failures).
        `fresh=True` skips the cache lookup (the new answer still replaces the cached one)."""
        run_id = uuid.uuid4()
        thread = thread_id or uuid.uuid4().hex
        question = (question or "").strip()
        started = self.rt.clock()
        self._reload_if_new_data()
        plan = self._cache_plan(question, user, thread)
        answer = None if (plan is None or fresh) else self._cache_get(plan, thread, started)
        if answer is None:
            answer = self._answer(question, user, thread, started)
            self._cache_put(plan, answer)
        answer.run_id = str(run_id)
        answer.est_cost = estimate_cost(answer.usage, self.rt.settings).usd
        if self.audit and question:
            answer.audit_ok = self._write_audit(run_id, question, user, answer)
        return answer

    # ---------------------------------------------------------------- the cache ---------------
    def _cache_plan(self, question: str, user: AppUser, thread: str) -> CachePlan | None:
        """Is this question eligible, and under which key? None means: do not use the cache."""
        if not self.cache or not question or len(question) > MAX_QUESTION_CHARS:
            return None
        if scrub(question) != question:  # looks like it contains personal data: never store it
            return None
        try:
            cfg = {"configurable": {"thread_id": thread}}
            if self.graph.get_state(cfg).values.get("history"):
                return None  # a follow-up depends on the conversation, not just its words
            norm = answer_cache.normalize(question)
            scope = answer_cache.scope_hash(user.allowed_cities)
            with self.rt.open_service() as conn:
                version = answer_cache.get_data_version(conn)
            return CachePlan(answer_cache.cache_key(norm, version, scope), norm, version, scope)
        except Exception as e:  # noqa: BLE001 - the cache must never break answering
            log.error("cache unavailable (%s); answering without it", type(e).__name__)
            return None

    def _cache_get(self, plan: CachePlan, thread: str, started: float) -> Answer | None:
        try:
            with self.rt.open_service() as conn:
                found = answer_cache.lookup(conn, plan.key)
            if found is None:
                return None
            data, created = found
            hit = replace(ANSWER.validate_python(data), thread_id=thread, cache_hit=True,
                          cached_at=created.isoformat(timespec="seconds"), usage=Usage(),
                          latency_ms=int((self.rt.clock() - started) * 1000))
            turn = Turn(question=plan.question_norm, rewritten=hit.understood_as, route=hit.route,
                        sql=hit.sql, answer=hit.answer[:500])
            self.graph.update_state({"configurable": {"thread_id": thread}},
                                    {"history": [turn]}, as_node="finalize")
            log.info("cache hit: version %s, answer from %s", plan.data_version, hit.cached_at)
            return hit
        except Exception as e:  # noqa: BLE001
            log.error("cache lookup failed (%s); answering without it", type(e).__name__)
            return None

    def _cache_put(self, plan: CachePlan | None, a: Answer) -> None:
        """Keep only answers that are complete, clean and standalone."""
        if plan is None or a.status != "answered" or a.fault_injected or a.cache_hit:
            return
        if a.route not in CACHEABLE_ROUTES:
            return
        if any("repaired automatically" not in w for w in a.warnings):
            return  # a warning means "treat with care"; do not repeat it from a cache
        try:
            data = ANSWER.dump_python(replace(a, run_id="", thread_id="", est_cost=None,
                                              audit_ok=True), mode="json")
            with self.rt.open_service() as conn:
                answer_cache.store(conn, plan.key, plan.question_norm, plan.data_version,
                                   plan.scope, data)
        except Exception as e:  # noqa: BLE001
            log.error("could not store the answer in the cache (%s)", type(e).__name__)

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
            warnings=a.warnings, fault_injected=a.fault_injected, cache_hit=a.cache_hit,
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
