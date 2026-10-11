"""Phase 13, part 1: turning what the assistant did into words and JSON."""
import json

import pytest

from swiggy_ai import presentation as pr
from tests.phase13 import builders as b


def test_the_spec_example_reads_as_a_story():
    a = b.healed()
    assert pr.timeline_text(a) == (
        "Attempt 1 failed: column total_amount does not exist -> The healer rewrote the query"
        " -> Attempt 2 succeeded (83 ms)")


def test_database_complaints_become_plain_words():
    assert pr.friendly_error("undefined_column", 'column "x" does not exist') == (
        "column x does not exist")
    assert pr.friendly_error("undefined_table", 'relation "analytics.nope" does not exist') == (
        "table analytics.nope does not exist")
    assert pr.friendly_error("timeout") == "the query took too long"
    assert "safety guard" in pr.friendly_error("guard", "Only SELECT queries are allowed")
    assert pr.friendly_error("never_heard_of_it") == "the query failed"
    assert pr.friendly_error("syntax", 'syntax error at or near "SELEC"').startswith(
        "the SQL could not be understood")


def test_every_error_class_the_system_can_produce_has_words():
    from swiggy_ai.agents.executor import SQLSTATE_CLASS
    from swiggy_ai.agents.state import HEALABLE
    for cls in {*SQLSTATE_CLASS.values(), *HEALABLE, "cost", "guard", "other"}:
        assert pr.ERROR_WORDS[cls], cls


def test_a_rejected_attempt_explains_which_check_and_why():
    a = b.answered(attempts=[
        b.attempt(1, status="rejected", error_class="result_check",
                  error_text="uses_real_clock; fan_out_suspected"),
        b.attempt(2, source="repair")])
    text = pr.timeline_text(a)
    assert "result checker rejected it: it used today's real date" in text
    assert "a join repeated rows" in text
    assert "rewrote it using the checker's feedback" in text


def test_a_demo_fault_is_labelled_and_a_clean_run_has_one_line():
    a = b.answered(attempts=[b.attempt(1, source="fault", status="error", error_class="syntax"),
                             b.attempt(2, source="healer")], fault_injected=True)
    assert "(a demo fault was injected here) failed" in pr.timeline_text(a)
    assert [line.text for line in pr.timeline(b.answered())] == ["Attempt 1 succeeded (40 ms)"]
    assert pr.timeline(b.clarify()) == []


def test_a_multi_step_answer_labels_each_step():
    a = b.answered(attempts=[b.attempt(1, item=1), b.attempt(1, item=2)])
    a.steps[0].question = "first part"
    lines = [x.text for x in pr.timeline(a)]
    assert lines[0] == "Step 1: first part" and "Step 2:" in lines[2]


def test_badges_tell_the_truth_about_what_happened():
    clean = {x.label for x in pr.badges(b.answered(), "Pune")}
    assert clean == {"All 4 result checks passed", "Scope: Pune"}
    assert any("1 check(s) flagged: fan out suspected" in x.label
               for x in pr.badges(b.with_flag()))
    h = [x for x in pr.badges(b.healed()) if x.tone == "warn"]
    assert h and "Repaired automatically (1 extra attempt)" in h[0].label
    cached = pr.badges(b.answered(cache_hit=True, cached_at="2026-10-11T10:00:00"))
    assert [x.label for x in cached] == ["From cache (answered 2026-10-11T10:00:00)"]
    assert any(x.tone == "demo" for x in pr.badges(b.answered(fault_injected=True)))
    assert pr.badges(b.clarify()) == []  # nothing to certify


def test_usage_line_covers_cost_unknown_known_and_cached():
    assert pr.usage_line(b.answered()) == "3 model call(s), 3,000 tokens in, 200 out"
    assert pr.usage_line(b.with_money()).endswith("about $0.0123")
    assert pr.usage_line(b.answered(cache_hit=True)) == "No model call (served from cache)"


def test_the_payload_is_plain_json_with_the_documented_keys():
    p = pr.payload(b.healed(), "t-1", "Pune")
    json.dumps(p)  # nothing exotic in there
    for key in ("answer", "sql", "attempts", "checks", "warnings", "usage", "status", "thread_id",
                "cache_hit", "timeline", "data", "badges"):
        assert key in p
    assert p["thread_id"] == "t-1" and p["healed"] is True
    assert [a["status"] for a in p["attempts"]] == ["error", "ok"]
    assert p["attempts"][0]["error_class"] == "undefined_column"
    assert p["usage"] == {"calls": 3, "tokens_in": 3000, "tokens_out": 200, "est_cost_usd": None}
    assert p["clarification"] is None
    assert pr.payload(b.clarify(), "t")["clarification"] == {"question": "Best by what?",
                                                             "options": ["revenue"]}


def test_database_text_in_the_payload_is_scrubbed_and_short():
    a = b.answered(attempts=[b.attempt(1, status="error", error_class="other",
                                       error_text="bad value for 9876543210 " + "x" * 900)])
    err = pr.payload(a, "t")["attempts"][0]["error"]
    assert "9876543210" not in err and len(err) <= 300


def test_result_rows_are_capped_and_decimals_become_numbers():
    p = pr.payload(b.with_money(), "t")
    rows = p["data"][0]["rows"]
    assert len(rows) == pr.MAX_ROWS_SHOWN and rows[0] == [12.5, "Pune", 0]
    assert p["usage"]["est_cost_usd"] == pytest.approx(0.0123)
    json.dumps(p)


def test_failed_steps_do_not_leak_into_the_data():
    a = b.answered()
    a.steps[0].ok = False
    assert pr.payload(a, "t")["data"] == []
