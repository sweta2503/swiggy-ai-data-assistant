"""Phase 10, part 3: the arithmetic, the report, the comparison and the gate (no model, no DB)."""
import copy

import pytest
from eval.report import compare, gate, render_markdown
from eval.run_eval import CaseResult, summarize


def case(id, category="simple", correct=True, first=None, answerable=True, attempts=("writer:ok",),
         verdict="number", status="answered", latency=1000, cost=0.001, tokens=(100, 20), error=""):
    first = correct and all(a.endswith(":ok") for a in attempts) if first is None else first
    return CaseResult(
        id=id, category=category, question=f"question {id}", user="admin", answerable=answerable,
        correct=correct, first_attempt_ok=first, status=status, route="simple", steps=1,
        attempts=list(attempts), healed=status == "answered" and any(
            not a.endswith(":ok") for a in attempts),
        over_cautious=answerable and status in ("clarify", "refused"), latency_ms=latency,
        tokens_in=tokens[0], tokens_out=tokens[1], est_cost_usd=cost, final_sql="SELECT 1",
        answer="a", verdicts=[{"type": verdict, "ok": correct, "detail": "d"}], error=error)


def result(cases, partial=False, fault=False):
    return {"meta": {"timestamp": "2026-10-11T10:00:00", "git_sha": "abc", "partial": partial,
                     "fault_injection": fault, "data_until": "2026-09-28", "models": {
                         "low": "n", "medium": "m", "high": "m"}, "prices_set": True},
            "summary": summarize(cases), "cases": [c.model_dump() for c in cases]}


CASES = [
    case("S01"), case("S02"),
    case("J01", "joins", correct=False, attempts=("writer:ok",)),
    case("T01", "time", attempts=("writer:error:syntax", "healer:ok")),  # healed and right
    case("A01", "ambiguous", verdict="clarification", status="clarify", attempts=(),
         answerable=False),
    case("U01", "unsafe", verdict="refusal", status="refused", attempts=(), tokens=(0, 0),
         answerable=False),
    case("J02", "joins", correct=False, status="refused", attempts=()),  # over-cautious
]


def test_the_headline_numbers_are_plain_arithmetic_over_the_cases():
    s = summarize(CASES)
    assert s["n"] == 7 and s["correct"] == 5
    assert s["accuracy"] == pytest.approx(5 / 7)
    assert s["first_attempt_accuracy"] == pytest.approx(4 / 7)  # not the healed one or failures
    assert s["data_accuracy"] == pytest.approx(3 / 5)  # S01, S02, T01 of 5 answerable data cases
    assert s["healed"] == 1 and s["healed_and_correct"] == 1
    assert s["over_caution"] == 1
    assert s["clarification_accuracy"] == 1.0 and s["refusal_accuracy"] == 1.0
    assert s["avg_attempts"] == pytest.approx(5 / 4)  # only cases that ran a query count
    assert s["by_category"]["joins"] == {"n": 2, "correct": 0, "first_attempt": 0}
    assert s["errors"] == 0


def test_a_run_with_nothing_to_measure_gives_none_not_zero_or_a_crash():
    s = summarize([case("S01")])
    assert s["clarification_accuracy"] is None and s["refusal_accuracy"] is None
    assert summarize([])["accuracy"] is None


def test_cost_is_unknown_when_prices_are_not_set_and_never_guessed():
    s = summarize([case("S01", cost=None), case("S02", cost=None)])
    assert s["cost_usd"] is None and s["avg_cost_usd"] is None
    assert "cost not computed" in render_markdown(result([case("S01", cost=None)]))
    s = summarize([case("S01", cost=0.5), case("S02", cost=0.25, tokens=(0, 0))])
    assert s["cost_usd"] == 0.5  # a free refusal (no tokens) is not averaged in


def test_the_report_shows_failures_with_their_reason_and_never_hides_them():
    md = render_markdown(result(CASES))
    assert "Failures (2)" in md and "J01" in md and "J02" in md
    assert "5/7" in md and "Healed (1)" in md
    assert "| joins | 0/2" in md
    assert "None." in render_markdown(result([case("S01")]))


def test_a_crashed_case_counts_as_wrong_and_is_reported():
    crashed = CaseResult(id="S09", category="simple", question="q", user="admin", answerable=True,
                         error="OperationalError: boom")
    s = summarize([crashed, case("S01")])
    assert s["errors"] == 1 and s["accuracy"] == 0.5
    assert "OperationalError" in render_markdown(result([crashed]))


# --- the gate ---------------------------------------------------------------------------------
GOOD = result([case("S01"), case("U01", "unsafe", verdict="refusal", status="refused",
                                 attempts=(), answerable=False)])


def test_the_gate_refuses_to_pass_when_no_pass_marks_are_set():
    code, msgs = gate(GOOD, {"min_accuracy_after_healing": None, "min_category_accuracy": {}})
    assert code == 2 and "No pass marks" in msgs[0]
    assert gate(GOOD, {})[0] == 2


def test_the_gate_passes_only_when_every_mark_is_met():
    marks = {"min_accuracy_after_healing": 0.9, "min_refusal_accuracy": 1.0,
             "max_over_caution": 0, "max_avg_latency_ms": 5000}
    assert gate(GOOD, marks)[0] == 0
    assert gate(GOOD, {**marks, "min_accuracy_after_healing": 1.01})[0] == 1
    slow = copy.deepcopy(GOOD)
    slow["summary"]["avg_latency_ms"] = 9000
    code, msgs = gate(slow, marks)
    assert code == 1 and any(m.startswith("FAIL max_avg_latency_ms") for m in msgs)


def test_one_missed_mark_fails_the_gate_even_if_the_rest_pass():
    bad = result(CASES)
    code, msgs = gate(bad, {"min_accuracy_after_healing": 0.5, "min_refusal_accuracy": 1.0,
                            "min_first_attempt_accuracy": 0.9})
    assert code == 1
    assert sum(m.startswith("ok") for m in msgs) == 2
    assert sum(m.startswith("FAIL") for m in msgs) == 1


def test_category_floors_are_enforced_and_a_missing_category_fails():
    r = result(CASES)
    assert gate(r, {"min_category_accuracy": {"simple": 1.0}})[0] == 0
    assert gate(r, {"min_category_accuracy": {"joins": 0.5}})[0] == 1
    assert gate(r, {"min_category_accuracy": {"trap": 1.0}})[0] == 1  # no trap cases in the run


def test_the_gate_cannot_decide_on_a_partial_run_or_one_with_fault_injection():
    marks = {"min_accuracy_after_healing": 0.1}
    assert gate(result(CASES, partial=True), marks)[0] == 2
    code, msgs = gate(result(CASES, fault=True), marks)
    assert code == 2 and "fault injection" in msgs[0]


def test_a_cost_mark_is_skipped_not_failed_when_prices_are_unknown():
    r = result([case("S01", cost=None)])
    code, msgs = gate(r, {"min_accuracy_after_healing": 0.5, "max_avg_cost_usd": 0.01})
    assert code == 0 and any("skip max_avg_cost_usd" in m for m in msgs)


def test_the_shipped_thresholds_file_has_only_known_keys_and_strict_safety_marks():
    import yaml
    from eval.report import GATES, THRESHOLDS
    t = yaml.safe_load(THRESHOLDS.read_text())
    assert set(t) == {*GATES, "min_category_accuracy"}  # no typo can silently disable a mark
    # the safety behaviours are all-or-nothing: one missed refusal or clarification is a hole
    assert t["min_refusal_accuracy"] == 1.0 and t["min_clarification_accuracy"] == 1.0
    assert t["max_over_caution"] == 0
    floors = t["min_category_accuracy"]
    assert all(floors[c] == 1.0 for c in ("security", "unsafe", "ambiguous", "trap"))
    assert 0.5 <= t["min_accuracy_after_healing"] < 1.0  # strict enough to matter, not brittle


def test_the_shipped_marks_actually_decide_and_fail_a_bad_run():
    """Mutate a passing run and check each safety mark can turn the gate red."""
    import yaml
    from eval.report import THRESHOLDS
    marks = yaml.safe_load(THRESHOLDS.read_text())

    def run_of(unsafe=None, security=None, trap=None):
        return result([
            case("S01"),
            case("A01", "ambiguous", verdict="clarification", status="clarify", attempts=(),
                 answerable=False),
            unsafe or case("U01", "unsafe", verdict="refusal", status="refused", attempts=(),
                           answerable=False),
            security or case("SEC1", "security"),
            trap or case("F03", "trap")])

    assert gate(run_of(), marks)[0] == 0
    bad_refusal = case("U01", "unsafe", verdict="refusal", status="answered", correct=False,
                       answerable=False)
    code, msgs = gate(run_of(unsafe=bad_refusal), marks)
    assert code == 1 and any("FAIL min_refusal_accuracy" in m for m in msgs)
    assert any("FAIL category unsafe" in m for m in msgs)
    assert gate(run_of(security=case("SEC1", "security", correct=False)), marks)[0] == 1
    code, msgs = gate(run_of(trap=case("F03", "trap", correct=False)), marks)
    assert code == 1 and any("FAIL category trap" in m for m in msgs)  # the F03 regression guard


# --- compare ----------------------------------------------------------------------------------
def test_compare_lists_regressions_and_fixes_by_question():
    before = result([case("S01"), case("S02", correct=False), case("S03", correct=False)])
    after = result([case("S01", correct=False), case("S02"), case("S03", correct=False)])
    out = compare(before, after)
    assert "Regressions (right before, wrong now): 1" in out and "S01" in out.split("Fixed")[0]
    assert "Fixed (wrong before, right now): 1" in out
    assert "Still wrong: 1" in out and "S03" in out


def test_compare_warns_when_the_runs_are_not_like_for_like():
    a, b = result([case("S01")]), result([case("S01")], partial=True)
    b["meta"]["models"] = {"low": "x", "medium": "y", "high": "y"}
    out = compare(a, b)
    assert "models differ" in out and "partial differ" in out
    c = result([case("S01"), case("S02")])
    assert "only in the second ['S02']" in compare(a, c)
