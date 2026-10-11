"""`make report`: how the assistant has been doing, from the audit trail."""
import argparse
import sys
from dataclasses import dataclass, field
from decimal import Decimal

import psycopg

from swiggy_ai.db import connect
from swiggy_ai.ops.audit import ensure_audit_tables
from swiggy_ai.ops.cost import NOT_COMPUTED


@dataclass
class Report:
    days: int
    total: int = 0
    by_status: dict[str, int] = field(default_factory=dict)
    by_route: dict[str, int] = field(default_factory=dict)
    healed: int = 0
    users: int = 0
    tokens_in: int = 0
    tokens_out: int = 0
    avg_latency_ms: float | None = None
    p50_latency_ms: float | None = None
    p95_latency_ms: float | None = None
    cost_total: Decimal | None = None
    cost_avg: Decimal | None = None
    runs_with_cost: int = 0  # questions that used the model AND have a computed cost
    model_runs: int = 0  # questions that used the model at all
    guard_rejections: int = 0
    fault_runs: int = 0
    cache_hits: int = 0
    error_classes: list[tuple[str, int]] = field(default_factory=list)
    slowest_queries: list[tuple[int, str, str, str]] = field(default_factory=list)

    @property
    def answered(self) -> int:
        return self.by_status.get("answered", 0)

    @property
    def success_rate(self) -> float | None:
        """Of the questions that were meant to get an answer, how many did."""
        tried = self.answered + self.by_status.get("failed", 0)
        return self.answered / tried if tried else None

    @property
    def heal_rate(self) -> float | None:
        """Of the answered questions, how many needed at least one repair."""
        return self.healed / self.answered if self.answered else None


def collect(conn: psycopg.Connection, days: int = 7) -> Report:
    ensure_audit_tables(conn)
    r = Report(days=days)
    window = "r.ts >= now() - make_interval(days => %s)"

    for status, n in conn.execute(
            f"SELECT status, count(*) FROM ops.assistant_runs r WHERE {window} GROUP BY 1",
            (days,)):
        r.by_status[status] = n
    r.total = sum(r.by_status.values())
    r.by_route = dict(conn.execute(
        f"SELECT coalesce(route, 'unknown'), count(*) FROM ops.assistant_runs r WHERE {window}"
        " GROUP BY 1 ORDER BY 2 DESC", (days,)).fetchall())
    if r.total == 0:
        return r

    (r.healed, r.users, r.tokens_in, r.tokens_out, r.avg_latency_ms, r.p50_latency_ms,
     r.p95_latency_ms, r.cost_total, r.cost_avg, r.runs_with_cost, r.model_runs,
     r.fault_runs, r.cache_hits) = conn.execute(
        f"SELECT count(*) FILTER (WHERE status = 'answered' AND healed), count(DISTINCT user_id),"
        " coalesce(sum(tokens_in), 0), coalesce(sum(tokens_out), 0), avg(latency_ms),"
        " percentile_cont(0.5) WITHIN GROUP (ORDER BY latency_ms),"
        " percentile_cont(0.95) WITHIN GROUP (ORDER BY latency_ms),"
        " sum(est_cost), avg(est_cost) FILTER (WHERE tokens_in + tokens_out > 0),"
        " count(est_cost) FILTER (WHERE tokens_in + tokens_out > 0),"
        " count(*) FILTER (WHERE tokens_in + tokens_out > 0),"
        " count(*) FILTER (WHERE fault_injected), count(*) FILTER (WHERE cache_hit)"
        f" FROM ops.assistant_runs r WHERE {window}", (days,)).fetchone()

    # Postgres returns avg() as an exact decimal; the report works in plain floats for timings.
    r.avg_latency_ms = float(r.avg_latency_ms) if r.avg_latency_ms is not None else None
    r.guard_rejections = conn.execute(
        "SELECT count(*) FROM ops.assistant_attempts a JOIN ops.assistant_runs r USING (run_id)"
        f" WHERE a.error_class = 'guard' AND {window}", (days,)).fetchone()[0]
    r.error_classes = conn.execute(
        "SELECT a.error_class, count(*) FROM ops.assistant_attempts a JOIN ops.assistant_runs r"
        f" USING (run_id) WHERE a.status <> 'ok' AND a.error_class IS NOT NULL AND {window}"
        " GROUP BY 1 ORDER BY 2 DESC, 1 LIMIT 5", (days,)).fetchall()
    r.slowest_queries = conn.execute(
        "SELECT a.latency_ms, r.user_id, a.status, a.sql FROM ops.assistant_attempts a JOIN"
        f" ops.assistant_runs r USING (run_id) WHERE a.latency_ms > 0 AND {window}"
        " ORDER BY a.latency_ms DESC LIMIT 5", (days,)).fetchall()
    return r


def pct(x: float | None) -> str:
    return "n/a" if x is None else f"{100 * x:.0f}%"


def render(r: Report) -> str:
    out = [f"Assistant report, last {r.days} days", "=" * 40]
    if r.total == 0:
        return "\n".join(out + [f"No questions in the last {r.days} days."])
    s = r.by_status
    out += [
        f"Questions         {r.total}  (from {r.users} user{'s' if r.users != 1 else ''})",
        f"  answered        {s.get('answered', 0)}",
        f"  asked to clarify {s.get('clarify', 0)}",
        f"  refused         {s.get('refused', 0)}",
        f"  failed          {s.get('failed', 0)}",
        f"Success rate      {pct(r.success_rate)}   (answered / answered + failed)",
        f"Heal rate         {pct(r.heal_rate)}   ({r.healed} answered only after a repair)",
        f"Guard rejections  {r.guard_rejections}   (queries the safety guard refused to run)",
    ]
    if r.cache_hits:
        share = pct(r.cache_hits / r.total)
        out.append(f"Cache hits        {r.cache_hits}   ({share} of questions were served from "
                   "the answer cache; no model call)")
    if r.fault_runs:
        out.append(f"Demo faults       {r.fault_runs} run(s) had a deliberate fault injected")
    out += ["", "Routes            " + ", ".join(f"{k} {v}" for k, v in r.by_route.items())]
    out += ["", "Top problems (query attempts that did not succeed)"]
    out += [f"  {n:>4}  {c}" for c, n in r.error_classes] or ["  none"]
    out += ["", "Slowest queries"]
    for ms, user, status, sql in r.slowest_queries:
        out.append(f"  {ms:>6} ms  {status:<8} {user:<18} {' '.join(sql.split())[:70]}")
    if r.avg_latency_ms is None:
        out += ["", "Speed             n/a (every question was served from the cache)"]
    else:
        out += ["", "Speed             "
                f"average {r.avg_latency_ms:,.0f} ms, median {r.p50_latency_ms:,.0f} ms, "
                f"95th percentile {r.p95_latency_ms:,.0f} ms (whole question"
                + (", cache hits excluded)" if r.cache_hits else ")")]
    out += ["", f"Tokens            {r.tokens_in:,} in, {r.tokens_out:,} out"]
    if r.model_runs == 0:
        out.append("Cost (USD)        $0 (no question used the model)")
    elif r.runs_with_cost == 0:
        out.append(f"Cost              {NOT_COMPUTED}")
    else:
        out.append(f"Cost (USD)        total ${r.cost_total:.4f}, average ${r.cost_avg:.4f} per "
                   "question that used the model")
        if r.runs_with_cost < r.model_runs:
            out.append(f"                  (computed for {r.runs_with_cost} of {r.model_runs} "
                       "such questions; the rest ran before prices were set)")
    return "\n".join(out)


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--days", type=int, default=7)
    a = p.parse_args()
    with connect("etl", autocommit=True) as conn:
        print(render(collect(conn, a.days)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
