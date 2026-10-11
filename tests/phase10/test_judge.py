"""Phase 10, part 1: the judge. If it is too lenient the eval flatters; too strict, it punishes
correct answers. Both directions are tested."""
import pytest
from eval.judge import (
    cell_matches,
    judge,
    judge_clarification,
    judge_contains,
    judge_number,
    judge_numbers_present,
    judge_refusal,
    judge_table,
    num_close,
    tolerance,
)
from eval.models import Expectation

from swiggy_ai.agents.service import Answer
from swiggy_ai.agents.state import Attempt, Clarification, StepResult


def answer(rows=None, text="", status="answered", columns=None, attempts=()):
    steps = [StepResult(id=1, question="q", ok=True, columns=columns or ["a"], rows=rows or [])]
    return Answer(status, text, steps=steps, attempts=list(attempts),
                  clarification=Clarification(question="which?") if status == "clarify" else None)


def ok_attempt():
    return Attempt(item=1, no=1, source="writer", sql="SELECT 1", status="ok")


# --- numbers ----------------------------------------------------------------------------------
def test_rounding_is_forgiven_but_a_wrong_number_is_not():
    assert num_close(34.43, 34.4347) and num_close(484.96, 484.9637869)
    assert num_close(63748004.83, 63748004.8300) and num_close(63748005, 63748004.83)
    assert not num_close(35.0, 34.4347) and not num_close(480, 484.96)
    assert not num_close(63700000, 63748004.83)


def test_a_small_rate_gets_a_tight_band_so_a_coarse_rounding_cannot_pass():
    assert num_close(0.0766, 0.07662588) and num_close(0.0808, 0.080782347)
    assert not num_close(0.08, 0.0766) and not num_close(0.1, 0.0766)
    assert tolerance(0.08) < tolerance(100)


def test_a_percentage_is_accepted_only_when_the_case_allows_it():
    assert not num_close(7.66, 0.0766) and num_close(7.66, 0.0766, accept_percent=True)
    assert num_close(0.0766, 0.0766, accept_percent=True)
    assert not num_close(8.5, 0.0766, accept_percent=True)  # 8.5% is not 7.66%


def test_judge_number_wants_one_row_that_contains_the_number():
    ref = [[3526]]
    assert judge_number(ref, [[3526]]).ok
    assert judge_number(ref, [["last week", 3526]]).ok  # extra columns are fine
    assert not judge_number(ref, [[3527]]).ok
    assert not judge_number(ref, [[3526], [100]]).ok  # two rows is not one answer
    assert not judge_number(ref, []).ok
    assert not judge_number(ref, [["3526"]]).ok  # a string is not a number


# --- tables -----------------------------------------------------------------------------------
REF = [["Bengaluru", 100.0], ["Mumbai", 200.0]]


def test_a_table_matches_regardless_of_row_and_column_order_unless_order_is_required():
    assert judge_table(REF, [["Mumbai", 200.0], ["Bengaluru", 100.0]]).ok
    assert judge_table(REF, [[100.0, "Bengaluru"], [200.0, "Mumbai"]]).ok
    assert not judge_table(REF, [["Mumbai", 200.0], ["Bengaluru", 100.0]], ordered=True).ok
    assert judge_table(REF, [["Bengaluru", 100.0], ["Mumbai", 200.0]], ordered=True).ok


def test_extra_columns_are_fine_but_missing_rows_or_wrong_values_are_not():
    assert judge_table(REF, [["Bengaluru", 100.0, 7], ["Mumbai", 200.0, 9]]).ok
    assert not judge_table(REF, [["Bengaluru", 100.0]]).ok  # a row missing
    assert not judge_table(REF, [["Bengaluru", 100.0], ["Mumbai", 200.0], ["Pune", 1.0]]).ok
    assert not judge_table(REF, [["Bengaluru", 100.0], ["Mumbai", 999.0]]).ok
    assert not judge_table(REF, [["Bengaluru", 100.0], ["Pune", 200.0]]).ok


def test_the_same_label_cannot_satisfy_two_reference_rows():
    ref = [["a", 1.0], ["a", 1.0]]
    assert not judge_table(ref, [["a", 1.0], ["b", 2.0]]).ok
    assert judge_table(ref, [["a", 1.0], ["a", 1.0]]).ok


def test_text_is_compared_loosely_and_dates_by_day():
    assert cell_matches("North Indian", " north indian ")
    assert cell_matches("SPOILED_FOOD", "spoiled food")
    assert cell_matches("2026-09-01", "2026-09-01 00:00:00")
    assert not cell_matches("2026-09-01", "2026-09-02")
    assert not cell_matches("Pune", "Mumbai") and cell_matches(None, None)


# --- numbers in text and rows -----------------------------------------------------------------
def test_numbers_present_looks_in_the_rows_and_in_the_written_answer():
    ref = [[6636022.91], [6446328.87]]
    assert judge_numbers_present(ref, answer([[6636022.91]], "Last month was 6,446,328.87.")).ok
    assert not judge_numbers_present(ref, answer([[6636022.91]], "It dropped.")).ok
    pct = [[0.0766], [0.0702]]
    assert judge_numbers_present(pct, answer(text="from 7.66% to 7.02%"), accept_percent=True).ok
    assert not judge_numbers_present(pct, answer(text="from 7.66% to 7.02%")).ok


def test_contains_needs_enough_of_the_reference_values_in_the_answer():
    ref = [["SPOILED_FOOD"], ["Ratti Corner"], ["Brar Tandoor"]]
    text = "The spoiled food refunds rose, mostly at Ratti Corner."
    assert judge_contains(ref, answer(text=text), min_match=2).ok
    assert not judge_contains(ref, answer(text=text), min_match=3).ok
    assert not judge_contains(ref, answer(text="Refunds went up a bit."), min_match=1).ok
    assert judge_contains([["Bengaluru"]], answer(text="BENGALURU is higher.")).ok


# --- clarify and refuse -----------------------------------------------------------------------
def test_clarification_means_a_question_and_no_query():
    assert judge_clarification(answer(status="clarify")).ok
    assert not judge_clarification(answer(status="answered")).ok
    assert not judge_clarification(answer(status="clarify", attempts=[ok_attempt()])).ok


def test_refusal_means_declined_and_nothing_ran():
    assert judge_refusal(answer(status="refused")).ok
    assert not judge_refusal(answer(status="answered")).ok
    ran = judge_refusal(answer(status="refused", attempts=[ok_attempt()]))
    assert not ran.ok and "a query ran" in ran.detail
    assert not judge_refusal(answer(status="clarify")).ok


def test_a_data_question_that_was_refused_or_clarified_is_wrong_not_crashing():
    exp = Expectation(type="number", reference_sql="S02.sql")
    for status in ("refused", "clarify", "failed"):
        v = judge(exp, [[1.0]], answer(status=status))
        assert not v.ok and status in v.detail


def test_judge_dispatches_each_expectation_type():
    rows, ans = [[5]], answer([[5]], "5")
    assert judge(Expectation(type="number", reference_sql="S01.sql"), rows, ans).ok
    assert judge(Expectation(type="table", reference_sql="J02.sql"), rows, ans).ok
    assert judge(Expectation(type="numbers_present", reference_sql="T03.sql"), rows, ans).ok
    assert judge(Expectation(type="contains", reference_sql="M03b.sql"), [["5"]], ans).ok
    assert judge(Expectation(type="clarification"), None, answer(status="clarify")).ok
    assert judge(Expectation(type="refusal"), None, answer(status="refused")).ok


def test_the_rows_judged_are_those_of_the_last_step_that_worked():
    from eval.judge import final_rows
    a = Answer("answered", "x", steps=[
        StepResult(id=1, question="a", ok=True, rows=[[1]]),
        StepResult(id=2, question="b", ok=False, failure="boom"),
        StepResult(id=3, question="c", ok=True, rows=[[3]])])
    assert final_rows(a) == [[3]]
    assert final_rows(Answer("failed", "", steps=[])) == []


@pytest.mark.parametrize("bad", [dict(type="number"), dict(type="clarification",
                                                           reference_sql="S01.sql"),
                                 dict(type="table", reference_sql="nope.sql")])
def test_a_broken_expectation_is_rejected_when_the_golden_file_loads(bad):
    with pytest.raises(ValueError):
        Expectation(**bad)
