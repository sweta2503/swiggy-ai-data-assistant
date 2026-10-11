"""Decide whether the assistant's answer matches the reference. Plain code; no model involved.

The reference comes from hand-written SQL on the gold tables. Matching rules are generous where a
correct answer can legitimately differ in form (extra columns, a rate shown as a percentage,
rounding) and strict where it cannot (the numbers, the rows, the order of a ranking).
"""
import re
from dataclasses import dataclass
from typing import Any

from swiggy_ai.agents.numbers import parse_numbers
from swiggy_ai.agents.service import Answer

ISO_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}")


@dataclass
class Verdict:
    type: str
    ok: bool
    detail: str = ""


def is_num(v: Any) -> bool:
    return isinstance(v, int | float) and not isinstance(v, bool)


def tolerance(ref: float) -> float:
    """How far a correct answer may differ from the reference because of rounding.
    Small numbers (rates) get a tight absolute band so 0.08 cannot pass for 0.0766."""
    return 5e-4 if abs(ref) < 1 else max(0.01, 5e-4 * abs(ref))


def num_close(got: float, ref: float, accept_percent: bool = False) -> bool:
    if isinstance(ref, int) and not isinstance(ref, bool):  # a count is exact: 3527 is not 3526
        return got == ref
    if abs(got - ref) <= tolerance(ref):
        return True
    return accept_percent and abs(got - ref * 100) <= tolerance(ref) * 100


def norm_text(v: Any) -> str:
    return re.sub(r"\s+", " ", str(v).strip().lower().replace("_", " "))


def cell_matches(ref: Any, got: Any, accept_percent: bool = False) -> bool:
    if is_num(ref):
        return is_num(got) and num_close(float(got), ref, accept_percent)
    if ref is None:
        return got is None
    r, g = str(ref).strip(), str(got).strip()
    if ISO_DATE.match(r) and ISO_DATE.match(g):  # a date and a midnight timestamp are the same day
        return r[:10] == g[:10]
    return norm_text(r) == norm_text(g)


def row_covers(ref_row: list[Any], got_row: list[Any], accept_percent: bool = False) -> bool:
    """Every cell of the reference row is found in the assistant's row (it may have more)."""
    unused = list(got_row)
    for ref in ref_row:
        hit = next((i for i, g in enumerate(unused) if cell_matches(ref, g, accept_percent)), None)
        if hit is None:
            return False
        unused.pop(hit)
    return True


def judge_number(ref_rows: list[list[Any]], got_rows: list[list[Any]],
                 accept_percent: bool = False) -> Verdict:
    ref = ref_rows[0][0]
    if len(got_rows) != 1:
        return Verdict("number", False, f"expected one row, got {len(got_rows)}")
    ok = any(is_num(c) and num_close(float(c), ref, accept_percent) for c in got_rows[0])
    return Verdict("number", ok, f"reference {ref}, got {got_rows[0]}")


def judge_table(ref_rows: list[list[Any]], got_rows: list[list[Any]], ordered: bool = False,
                accept_percent: bool = False) -> Verdict:
    if len(got_rows) != len(ref_rows):
        return Verdict("table", False, f"expected {len(ref_rows)} rows, got {len(got_rows)}")
    used: set[int] = set()
    positions = []
    for ref in ref_rows:
        hit = next((i for i, g in enumerate(got_rows)
                    if i not in used and row_covers(ref, g, accept_percent)), None)
        if hit is None:
            return Verdict("table", False, f"no matching row for {ref}")
        used.add(hit)
        positions.append(hit)
    if ordered and positions != sorted(positions):
        return Verdict("table", False, "the rows are right but not in the required order")
    return Verdict("table", True, f"{len(ref_rows)} rows match")


def answer_numbers(answer: Answer) -> list[float]:
    """Every number the user could read: the result cells of each step, and the written answer."""
    nums = [float(c) for s in answer.steps if s.ok for row in s.rows for c in row if is_num(c)]
    return nums + [v for _, v, _, _ in parse_numbers(answer.answer)]


def judge_numbers_present(ref_rows: list[list[Any]], answer: Answer,
                          accept_percent: bool = False) -> Verdict:
    have = answer_numbers(answer)
    missing = [r[0] for r in ref_rows
               if not any(num_close(h, r[0], accept_percent) for h in have)]
    return Verdict("numbers_present", not missing,
                   "all numbers found" if not missing else f"missing {missing}")


def judge_contains(ref_rows: list[list[Any]], answer: Answer,
                   min_match: int | None = None) -> Verdict:
    values = [str(r[0]) for r in ref_rows]
    text = norm_text(answer.answer)
    found = [v for v in values if norm_text(v) in text]
    need = min_match or len(values)
    return Verdict("contains", len(found) >= need,
                   f"found {found} of {values}, needed {need}")


def judge_clarification(answer: Answer) -> Verdict:
    ran_query = any(a.status == "ok" for a in answer.attempts)
    ok = answer.status == "clarify" and answer.clarification is not None and not ran_query
    return Verdict("clarification", ok, f"status was '{answer.status}'")


def judge_refusal(answer: Answer) -> Verdict:
    ran_query = any(a.status == "ok" for a in answer.attempts)
    ok = answer.status == "refused" and not ran_query
    return Verdict("refusal", ok, f"status was '{answer.status}'"
                   + (", and a query ran" if ran_query else ""))


def final_rows(answer: Answer) -> list[list[Any]]:
    """The rows behind the answer: those of the last step that worked."""
    ok = [s for s in answer.steps if s.ok]
    return ok[-1].rows if ok else []


def judge(exp, ref_rows: list[list[Any]] | None, answer: Answer) -> Verdict:
    """Apply one expectation (an eval.models.Expectation) to an answer."""
    if exp.type == "clarification":
        return judge_clarification(answer)
    if exp.type == "refusal":
        return judge_refusal(answer)
    if answer.status != "answered":
        return Verdict(exp.type, False, f"no answer: status was '{answer.status}'")
    if exp.type == "number":
        return judge_number(ref_rows, final_rows(answer), exp.accept_percent)
    if exp.type == "table":
        return judge_table(ref_rows, final_rows(answer), exp.ordered, exp.accept_percent)
    if exp.type == "numbers_present":
        return judge_numbers_present(ref_rows, answer, exp.accept_percent)
    return judge_contains(ref_rows, answer, exp.min_match)
