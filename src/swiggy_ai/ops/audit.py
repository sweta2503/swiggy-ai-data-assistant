"""Write the audit trail: what was asked, what ran, what it cost. Never store personal data."""
import logging
import uuid
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

import psycopg
from psycopg.types.json import Jsonb

from swiggy_ai.logging_setup import scrub
from swiggy_ai.pipeline.runner import SQL_ROOT

log = logging.getLogger("audit")
MAX_TEXT = 1000


def clean(text: str | None, limit: int = MAX_TEXT) -> str | None:
    """Scrub personal data and secrets from text, and cut it to a sensible length."""
    if text is None:
        return None
    text = scrub(text)
    return text if len(text) <= limit else text[: limit - 1] + "…"


@dataclass
class AttemptRecord:
    attempt_no: int
    step: int
    source: str
    sql: str
    status: str
    error_class: str = ""
    error_text: str = ""
    latency_ms: int = 0


@dataclass
class RunRecord:
    run_id: uuid.UUID
    thread_id: str
    user_id: str
    scope: str
    question: str
    status: str
    working_question: str = ""
    route: str = ""
    healed: bool = False
    steps: int = 0
    final_sql: str = ""
    latency_ms: int = 0
    tokens_in: int = 0
    tokens_out: int = 0
    est_cost: Decimal | None = None
    usage_by_step: dict[str, Any] = field(default_factory=dict)
    models: dict[str, str] = field(default_factory=dict)
    checks: list[dict[str, Any]] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    fault_injected: bool = False
    cache_hit: bool = False
    attempts: list[AttemptRecord] = field(default_factory=list)


def ensure_audit_tables(conn: psycopg.Connection) -> None:
    conn.execute((SQL_ROOT / "ops" / "30_audit.sql").read_text())


def record_run(conn: psycopg.Connection, r: RunRecord) -> None:
    """Insert the run and its attempts together, scrubbed. All or nothing."""
    with conn.transaction():
        conn.execute(
            "INSERT INTO ops.assistant_runs (run_id, thread_id, user_id, scope, question,"
            " working_question, route, status, healed, attempts_count, steps, final_sql,"
            " latency_ms, tokens_in, tokens_out, est_cost, usage_by_step, models, checks,"
            " warnings, fault_injected, cache_hit)"
            " VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,"
            " %s, %s, %s, %s)",
            (r.run_id, r.thread_id, r.user_id, r.scope, clean(r.question),
             clean(r.working_question), r.route, r.status, r.healed, len(r.attempts), r.steps,
             clean(r.final_sql, 4000), r.latency_ms, r.tokens_in, r.tokens_out, r.est_cost,
             Jsonb(r.usage_by_step), Jsonb(r.models),
             Jsonb([{k: clean(v) if isinstance(v, str) else v for k, v in c.items()}
                    for c in r.checks]),
             Jsonb([clean(w) for w in r.warnings]), r.fault_injected, r.cache_hit))
        if r.attempts:
            with conn.cursor() as cur:
                cur.executemany(
                    "INSERT INTO ops.assistant_attempts (run_id, attempt_no, step, source, sql,"
                    " status, error_class, error_text, latency_ms)"
                    " VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)",
                    [(r.run_id, a.attempt_no, a.step, a.source, clean(a.sql, 4000), a.status,
                      a.error_class or None, clean(a.error_text, 500), a.latency_ms)
                     for a in r.attempts])
