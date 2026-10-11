"""`make eval`: ask the assistant every golden question, with the real model, and score it.

Honest by construction:
  * the expected answers come from hand-written SQL, never from the assistant
  * demo fault injection is forced OFF (and every answer is checked to confirm it)
  * nothing is tuned to a question: failures are reported, with the SQL the model wrote
"""
import argparse
import json
import subprocess
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from eval.judge import Verdict, judge
from eval.models import EVAL_DIR, Golden, Question, load_golden
from swiggy_ai.agents.service import Answer, Assistant
from swiggy_ai.config import get_settings
from swiggy_ai.db import connect
from swiggy_ai.security.scope import AppUser

RESULTS_DIR = EVAL_DIR / "results"


class FaultInjectionError(RuntimeError):
    """The assistant reported that a demo fault was injected. That voids the whole run."""


class CaseResult(BaseModel):
    id: str
    category: str
    question: str
    user: str
    answerable: bool
    correct: bool = False
    first_attempt_ok: bool = False  # right, and no failed or rejected attempt along the way
    status: str = ""
    route: str = ""
    steps: int = 0
    attempts: list[str] = []  # "source:status[:error_class]"
    healed: bool = False
    over_cautious: bool = False  # an answerable question that was refused or sent to clarify
    latency_ms: int = 0
    tokens_in: int = 0
    tokens_out: int = 0
    est_cost_usd: float | None = None
    final_sql: str = ""
    answer: str = ""
    warnings: list[str] = []
    verdicts: list[dict[str, Any]] = []
    reference: list[Any] = []  # a preview of what the right answer was
    error: str = ""


def run_reference(conn, question: Question) -> dict[int, list[list[Any]]]:
    """Run each expectation's reference SQL. Results use the same plain-Python types as answers."""
    from swiggy_ai.agents.runtime import jsonable
    out = {}
    for i, exp in enumerate(question.expected):
        if exp.reference_sql:
            sql = (EVAL_DIR / "reference_sql" / exp.reference_sql).read_text()
            out[i] = [[jsonable(v) for v in row] for row in conn.execute(sql).fetchall()]
    return out


def evaluate(question: Question, answer: Answer, refs: dict[int, list[list[Any]]]) -> CaseResult:
    verdicts: list[Verdict] = [judge(e, refs.get(i), answer)
                               for i, e in enumerate(question.expected)]
    correct = all(v.ok for v in verdicts)
    attempts = [f"{a.source}:{a.status}" + (f":{a.error_class}" if a.error_class else "")
                for a in answer.attempts]
    clean = all(a.status == "ok" for a in answer.attempts)
    return CaseResult(
        id=question.id, category=question.category, question=question.question,
        user=question.user, answerable=question.answerable, correct=correct,
        first_attempt_ok=correct and clean, status=answer.status, route=answer.route,
        steps=len(answer.steps), attempts=attempts,
        healed=answer.status == "answered" and not clean,
        over_cautious=question.answerable and answer.status in ("clarify", "refused"),
        latency_ms=answer.latency_ms, tokens_in=answer.usage.tokens_in,
        tokens_out=answer.usage.tokens_out,
        est_cost_usd=None if answer.est_cost is None else float(answer.est_cost),
        final_sql=answer.sql, answer=answer.answer[:700], warnings=answer.warnings,
        verdicts=[v.__dict__ for v in verdicts],
        reference=[r[:6] for r in next(iter(refs.values()), [])][:6])


def summarize(cases: list[CaseResult]) -> dict[str, Any]:
    """The numbers the report and the gate use. Pure arithmetic over the case results."""
    n = len(cases)
    answerable = [c for c in cases if c.answerable]
    with_attempts = [c for c in cases if c.attempts]
    clar = [c for c in cases if "clarification" in {v["type"] for v in c.verdicts}]
    refuse = [c for c in cases if "refusal" in {v["type"] for v in c.verdicts}]
    costs = [c.est_cost_usd for c in cases if c.tokens_in + c.tokens_out > 0]
    latencies = sorted(c.latency_ms for c in cases)

    def rate(num: int, den: int) -> float | None:
        return num / den if den else None

    by_cat: dict[str, dict[str, int]] = {}
    for c in cases:
        row = by_cat.setdefault(c.category, {"n": 0, "correct": 0, "first_attempt": 0})
        row["n"] += 1
        row["correct"] += c.correct
        row["first_attempt"] += c.first_attempt_ok
    return {
        "n": n,
        "correct": sum(c.correct for c in cases),
        "accuracy": rate(sum(c.correct for c in cases), n),  # after healing
        "first_attempt_accuracy": rate(sum(c.first_attempt_ok for c in cases), n),
        "data_accuracy": rate(sum(c.correct for c in answerable), len(answerable)),
        "avg_attempts": rate(sum(len(c.attempts) for c in with_attempts), len(with_attempts)),
        "healed": sum(c.healed for c in cases),
        "healed_and_correct": sum(c.healed and c.correct for c in cases),
        "clarification_accuracy": rate(sum(c.correct for c in clar), len(clar)),
        "refusal_accuracy": rate(sum(c.correct for c in refuse), len(refuse)),
        "over_caution": sum(c.over_cautious for c in cases),
        "avg_latency_ms": rate(sum(latencies), n),
        "p95_latency_ms": latencies[min(n - 1, int(0.95 * n))] if n else None,
        "tokens_in": sum(c.tokens_in for c in cases),
        "tokens_out": sum(c.tokens_out for c in cases),
        "cost_usd": sum(costs) if costs and all(x is not None for x in costs) else None,
        "avg_cost_usd": (sum(costs) / len(costs)
                         if costs and all(x is not None for x in costs) else None),
        "errors": sum(bool(c.error) for c in cases),
        "by_category": by_cat,
    }


def git_sha() -> str:
    try:
        return subprocess.run(["git", "rev-parse", "--short", "HEAD"], capture_output=True,
                              text=True, check=True).stdout.strip()
    except Exception:  # noqa: BLE001
        return "unknown"


def select(golden: Golden, only: str | None, category: str | None,
           limit: int | None) -> list[Question]:
    qs = golden.questions
    if only:
        wanted = {x.strip() for x in only.split(",")}
        qs = [q for q in qs if q.id in wanted]
    if category:
        qs = [q for q in qs if q.category == category]
    return qs[:limit] if limit else qs


def run(questions: list[Question], golden: Golden, assistant: Assistant, ref_conn,
        log=print) -> list[CaseResult]:
    results = []
    for i, q in enumerate(questions, start=1):
        started = time.time()
        refs = run_reference(ref_conn, q)
        try:
            scope = golden.users[q.user]
            answer = assistant.ask(q.question, AppUser(f"eval_{q.user}", q.user, scope),
                                   thread_id=f"eval-{q.id}-{int(started)}")
            if answer.fault_injected:
                raise FaultInjectionError("fault injection must never be active in an eval")
            case = evaluate(q, answer, refs)
        except FaultInjectionError:
            raise
        except Exception as e:  # noqa: BLE001 - one broken case must not stop the run
            case = CaseResult(id=q.id, category=q.category, question=q.question, user=q.user,
                              answerable=q.answerable, error=f"{type(e).__name__}: {str(e)[:200]}")
        results.append(case)
        mark = "ok  " if case.correct else "FAIL"
        log(f"[{i:>2}/{len(questions)}] {mark} {q.id:<5} {case.status or 'error':<9} "
            f"{case.latency_ms / 1000:>5.1f}s  {q.question[:60]}")
    return results


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--only", help="comma-separated question ids, e.g. S01,J02")
    p.add_argument("--category")
    p.add_argument("--limit", type=int)
    p.add_argument("--dry-run", action="store_true",
                   help="check the golden file and run every reference query; no model is called")
    p.add_argument("--out", type=Path, default=RESULTS_DIR)
    a = p.parse_args(argv)

    golden = load_golden()
    questions = select(golden, a.only, a.category, a.limit)
    if not questions:
        print("no questions selected")
        return 1
    partial = len(questions) != len(golden.questions)

    with connect("etl", autocommit=True) as ref_conn:
        if a.dry_run:
            for q in questions:
                refs = run_reference(ref_conn, q)
                shown = {i: f"{len(r)} row(s)" for i, r in refs.items()}
                print(f"{q.id:<5} {q.category:<10} reference {shown or 'n/a (no data answer)'}")
            print(f"\n{len(questions)} questions checked; no model was called")
            return 0

        settings = get_settings()
        if settings.demo_fault_injection:
            print("note: DEMO_FAULT_INJECTION is on in .env; it is ignored for the evaluation")
        settings = settings.model_copy(update={"demo_fault_injection": False})
        from swiggy_ai.agents.runtime import Runtime
        # never the cache: an evaluation must measure the real thing
        assistant = Assistant(Runtime.create(settings), audit=False, cache=False)
        data_until = ref_conn.execute("SELECT max(order_date_ist) FROM gold.fact_orders"
                                      ).fetchone()[0]
        started = datetime.now(UTC)
        sql_model = settings.openai_model_medium or settings.openai_model
        print(f"Evaluating {len(questions)} questions (SQL model: {sql_model})\n")
        cases = run(questions, golden, assistant, ref_conn)

    summary = summarize(cases)
    models = assistant._models()
    result = {
        "meta": {"timestamp": started.isoformat(timespec="seconds"), "git_sha": git_sha(),
                 "models": models, "partial": partial, "fault_injection": False,
                 "data_until": str(data_until), "max_heal_attempts": settings.max_heal_attempts,
                 "prices_set": summary["cost_usd"] is not None},
        "summary": summary, "cases": [c.model_dump() for c in cases]}
    a.out.mkdir(parents=True, exist_ok=True)
    stamp = started.strftime("%Y%m%d-%H%M%S")
    (a.out / f"{stamp}.json").write_text(json.dumps(result, indent=1, default=str))
    from eval.report import render_markdown
    md = render_markdown(result)
    (a.out / f"{stamp}.md").write_text(md)
    print("\n" + md)
    print(f"\nSaved eval/results/{stamp}.json and .md")
    return 0


if __name__ == "__main__":
    sys.exit(main())
