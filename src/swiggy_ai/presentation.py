"""How an Answer is shown to people: the API, the chat UI and the command line all use this.

Nothing here decides anything; it only turns what the assistant did into plain language, so the
three interfaces cannot disagree about what "Attempt 2 succeeded" means.
"""
import re
from dataclasses import dataclass
from typing import Any

from swiggy_ai.agents.service import Answer
from swiggy_ai.logging_setup import scrub

MAX_ROWS_SHOWN = 50
MAX_TEXT = 300

# The result checker looks at four things on every query (values, dates, filter spellings and
# join fan-out). The guard and the cost check run before it. A check "raised" only when it flagged.
RESULT_CHECKS = 4

ERROR_WORDS = {
    "syntax": "the SQL could not be understood",
    "undefined_column": "a column does not exist",
    "undefined_table": "a table does not exist",
    "ambiguous_column": "a column name was ambiguous",
    "datatype_mismatch": "a value had the wrong type",
    "grouping_error": "the grouping was wrong",
    "timeout": "the query took too long",
    "permission_denied": "that is not allowed",
    "cost": "the query looked too expensive to run",
    "guard": "the safety guard refused it",
    "other": "the query failed",
}
CHECK_WORDS = {
    "uses_real_clock": "it used today's real date instead of the date the data ends",
    "period_not_anchored": "it did not measure the period from the date the data ends",
    "fan_out_suspected": "a join repeated rows, so a total looked inflated",
    "unknown_value": "it filtered on a value that does not exist",
    "item_lines_counted_as_orders": "it counted item lines where the question asked about orders",
    "empty_result": "the query returned no rows",
    "all_null_result": "every value in the result was empty",
    "row_cap": "the result was cut at the row limit",
    "negative_revenue": "a revenue figure was negative",
    "rate_out_of_range": "a rate fell outside 0 to 100%",
    "model_sanity_read": "a second read of the result had a concern",
}


def friendly_error(error_class: str, error_text: str = "") -> str:
    """The database's complaint in plain words, e.g. 'column total_amount does not exist'."""
    text = " ".join((error_text or "").split())
    m = re.search(r'(column|relation|table) "?([\w.]+)"? does not exist', text)
    if m:
        return f"{m.group(1) if m.group(1) != 'relation' else 'table'} {m.group(2)} does not exist"
    base = ERROR_WORDS.get(error_class, ERROR_WORDS["other"])
    return f"{base} ({text[:120]})" if text and error_class in ("syntax", "guard", "cost") else base


def friendly_checks(names: str) -> str:
    parts = [n.strip() for n in (names or "").split(";") if n.strip()]
    return "; ".join(CHECK_WORDS.get(n, n.replace("_", " ")) for n in parts) or "a check flagged it"


@dataclass(frozen=True)
class Line:
    kind: str  # attempt | action
    tone: str  # ok | error | rejected | note
    text: str


def timeline(a: Answer) -> list[Line]:
    """Each attempt and each repair, in order, in plain language."""
    lines: list[Line] = []
    questions = {s.id: s.question for s in a.steps}
    multi = len({t.item for t in a.attempts}) > 1
    last_item = None
    for t in a.attempts:
        if multi and t.item != last_item:
            lines.append(Line("action", "note", f"Step {t.item}: {questions.get(t.item, '')}"))
        last_item = t.item
        if t.no > 1:
            lines.append(Line("action", "note", {
                "healer": "The healer rewrote the query",
                "repair": "The SQL writer rewrote it using the checker's feedback",
                "writer": "The SQL writer tried again",
            }.get(t.source, "The query was rewritten")))
        who = " (a demo fault was injected here)" if t.source == "fault" else ""
        if t.status == "ok":
            lines.append(Line("attempt", "ok",
                              f"Attempt {t.no}{who} succeeded ({t.latency_ms} ms)"))
        elif t.status == "rejected":
            lines.append(Line("attempt", "rejected", f"Attempt {t.no}{who} ran, but the result "
                              f"checker rejected it: {friendly_checks(t.error_text)}"))
        else:
            lines.append(Line("attempt", "error", f"Attempt {t.no}{who} failed: "
                              f"{friendly_error(t.error_class, t.error_text)}"))
    return lines


def timeline_text(a: Answer) -> str:
    """One line: 'Attempt 1 failed: ... -> The healer rewrote the query -> Attempt 2 succeeded'."""
    return " -> ".join(line.text for line in timeline(a))


@dataclass(frozen=True)
class Badge:
    label: str
    tone: str  # good | warn | info | demo


def badges(a: Answer, scope: str = "") -> list[Badge]:
    out: list[Badge] = []
    if a.cache_hit:
        out.append(Badge(f"From cache (answered {a.cached_at})", "info"))
    if a.status == "answered" and not a.cache_hit:
        flagged = [c for c in a.checks if not c.passed]
        if flagged:
            out.append(Badge(f"{len(flagged)} check(s) flagged: "
                             + ", ".join(sorted({c.name.replace('_', ' ') for c in flagged})),
                             "warn"))
        else:
            out.append(Badge(f"All {RESULT_CHECKS} result checks passed", "good"))
    if a.healed:
        extra = len(a.attempts) - len({t.item for t in a.attempts})
        plural = "s" if extra != 1 else ""
        out.append(Badge(f"Repaired automatically ({extra} extra attempt{plural})", "warn"))
    if a.fault_injected:
        out.append(Badge("DEMO: a fault was injected", "demo"))
    if scope:
        out.append(Badge(f"Scope: {scope}", "info"))
    return out


def usage_line(a: Answer) -> str:
    if a.cache_hit:
        return "No model call (served from cache)"
    u = a.usage
    cost = "" if a.est_cost is None else f", about ${a.est_cost:.4f}"
    return f"{u.calls} model call(s), {u.tokens_in:,} tokens in, {u.tokens_out:,} out{cost}"


def _text(value: Any, limit: int = MAX_TEXT) -> str:
    return scrub(" ".join(str(value or "").split()))[:limit]


def payload(a: Answer, thread_id: str, scope: str = "") -> dict[str, Any]:
    """The JSON the API returns (and `cli --json` prints). Text that came from the database is
    scrubbed and shortened; nothing here carries a key, a stack trace or an internal id."""
    return {
        "status": a.status,
        "answer": a.answer,
        "sql": a.sql,
        "thread_id": thread_id,
        "run_id": a.run_id,
        "cache_hit": a.cache_hit,
        "healed": a.healed,
        "route": a.route,
        "understood_as": a.understood_as,
        "clarification": ({"question": a.clarification.question,
                           "options": a.clarification.options} if a.clarification else None),
        "attempts": [{"step": t.item, "attempt": t.no, "source": t.source, "status": t.status,
                      "error_class": t.error_class or None, "error": _text(t.error_text) or None,
                      "sql": t.sql, "latency_ms": t.latency_ms} for t in a.attempts],
        "timeline": [line.text for line in timeline(a)],
        "checks": [{"name": c.name, "severity": c.severity, "passed": c.passed,
                    "detail": _text(c.detail)} for c in a.checks],
        "warnings": a.warnings,
        "data": [{"question": s.question, "columns": s.columns, "truncated": s.truncated,
                  "rows": _jsonable_rows(s.rows[:MAX_ROWS_SHOWN])} for s in a.steps if s.ok],
        "usage": {"calls": a.usage.calls, "tokens_in": a.usage.tokens_in,
                  "tokens_out": a.usage.tokens_out,
                  "est_cost_usd": None if a.est_cost is None else float(a.est_cost)},
        "latency_ms": a.latency_ms,
        "fault_injected": a.fault_injected,
        "badges": [{"label": b.label, "tone": b.tone} for b in badges(a, scope)],
    }


def _jsonable_rows(rows: list[list[Any]]) -> list[list[Any]]:
    from decimal import Decimal
    return [[float(v) if isinstance(v, Decimal) else (v if isinstance(v, int | float | bool)
                                                       or v is None else str(v)) for v in r]
            for r in rows]
