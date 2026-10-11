"""Phase 9, part 2: every question leaves a record, and the record holds no personal data.

The assistant runs for real (scripted model, real database); its audit rows go into a transaction
that is rolled back, so the dev audit trail stays clean.
"""
import json
import logging
import re
from decimal import Decimal

import pytest

from swiggy_ai.agents.clarifier import ClarifyOut
from swiggy_ai.agents.healer import Repair
from swiggy_ai.agents.narrator import Narration
from swiggy_ai.agents.router import RouteDecision
from swiggy_ai.agents.sql_writer import SQLDraft
from swiggy_ai.llm import LLMError
from swiggy_ai.security.scope import AppUser

pytestmark = pytest.mark.db
ADMIN = AppUser("tester", "Tester", "ALL")
NET = ("SELECT SUM(order_value - discount) FILTER (WHERE status = 'delivered') AS net_revenue "
       "FROM analytics.orders")
ORDERS = "SELECT count(*) AS orders FROM analytics.orders"
PHONE = re.compile(r"(?<!\d)[6-9]\d{9}(?!\d)")


def run_row(conn, run_id):
    cur = conn.execute("SELECT * FROM ops.assistant_runs WHERE run_id = %s", (run_id,))
    cols = [d.name for d in cur.description]
    row = cur.fetchone()
    return dict(zip(cols, row, strict=True)) if row else None


def attempts(conn, run_id):
    return conn.execute("SELECT attempt_no, step, source, status, error_class, sql, latency_ms"
                        " FROM ops.assistant_attempts WHERE run_id = %s ORDER BY attempt_no",
                        (run_id,)).fetchall()


# --- one record per question ------------------------------------------------------------------
def test_an_answered_question_is_recorded_with_everything_the_report_needs(make_assistant, held):
    svc, h = make_assistant()
    h.llm.say(SQLDraft, SQLDraft(sql=NET)).say(Narration, Narration(answer="Here it is."))
    a = svc.ask("What is our net revenue?", AppUser("priya", "Priya", "Pune,Mumbai"),
                thread_id="th-1")
    r = run_row(held, a.run_id)

    assert a.audit_ok and r is not None
    assert (r["user_id"], r["scope"], r["thread_id"]) == ("priya", "Pune,Mumbai", "th-1")
    assert (r["question"], r["route"], r["status"]) == ("What is our net revenue?", "simple",
                                                        "answered")
    assert r["healed"] is False and r["attempts_count"] == 1 and r["steps"] == 1
    assert r["final_sql"].endswith("LIMIT 500")
    assert (r["tokens_in"], r["tokens_out"]) == (300, 60)  # router + writer + narrator
    assert set(r["usage_by_step"]) == {"router", "sql_writer", "narrator"}
    assert r["latency_ms"] >= 0 and r["fault_injected"] is False
    assert r["checks"] == [] and r["warnings"] == []
    assert r["models"]["low"] and r["models"]["medium"]  # which models answered
    assert [(x[0], x[2], x[3]) for x in attempts(held, a.run_id)] == [(1, "writer", "ok")]


def test_the_run_id_returned_to_the_caller_finds_the_record(make_assistant, held):
    svc, h = make_assistant()
    h.llm.say(SQLDraft, SQLDraft(sql=ORDERS)).say(Narration, Narration(answer="ok"))
    a = svc.ask("How many orders?", ADMIN)
    assert re.fullmatch(r"[0-9a-f-]{36}", a.run_id)
    assert run_row(held, a.run_id)["user_id"] == "tester"


def test_a_repaired_question_records_every_attempt_in_order(make_assistant, held):
    svc, h = make_assistant()
    h.llm.say(SQLDraft, SQLDraft(sql=NET.replace("order_value", "total_amount")))
    h.llm.say(Repair, Repair(sql=NET, changed="fixed the column")).say(
        Narration, Narration(answer="ok"))
    a = svc.ask("What is our net revenue?", ADMIN)
    r = run_row(held, a.run_id)
    assert r["healed"] is True and r["attempts_count"] == 2
    rows = attempts(held, a.run_id)
    assert [(x[0], x[2], x[3], x[4]) for x in rows] == [
        (1, "writer", "error", "undefined_column"), (2, "healer", "ok", None)]
    assert "total_amount" in rows[0][5] and rows[0][1] == 1


def test_a_demo_fault_is_recorded_as_such(make_assistant, held):
    svc, h = make_assistant(demo_fault_injection=True)
    h.llm.say(SQLDraft, SQLDraft(sql=NET)).say(Repair, Repair(sql=NET)).say(
        Narration, Narration(answer="ok"))
    r = run_row(held, svc.ask("net revenue", ADMIN).run_id)
    assert r["fault_injected"] is True
    assert any("Fault injected" in w for w in r["warnings"])


def test_a_multi_step_question_records_each_steps_attempts(make_assistant, held):
    from swiggy_ai.agents.planner import PlanOut
    from swiggy_ai.agents.state import SubQuestion
    svc, h = make_assistant()
    h.llm.say(RouteDecision, RouteDecision(route="multi_step", question="q"))
    h.llm.say(PlanOut, PlanOut(sub_questions=[SubQuestion(id=1, question="a"),
                                              SubQuestion(id=2, question="b")]))
    h.llm.say(SQLDraft, SQLDraft(sql=ORDERS), SQLDraft(sql=ORDERS)).say(
        Narration, Narration(answer="Both [1] [2]."))
    a = svc.ask("q", ADMIN)
    r = run_row(held, a.run_id)
    assert r["route"] == "multi_step" and r["steps"] == 2
    assert [(x[0], x[1]) for x in attempts(held, a.run_id)] == [(1, 1), (2, 2)]


def test_result_checker_findings_are_recorded(make_assistant, held):
    svc, h = make_assistant()
    h.llm.say(SQLDraft, SQLDraft(sql="SELECT 1.5 AS cancellation_rate")).say(
        Narration, Narration(answer="Odd."))
    r = run_row(held, svc.ask("What is the cancellation rate?", ADMIN).run_id)
    assert r["checks"][0]["name"] == "rate_out_of_range" and r["checks"][0]["severity"] == "warn"
    assert any("between 0 and 1" in w for w in r["warnings"])


# --- refusals, clarifications and failures are audited too ------------------------------------
def test_a_refusal_is_recorded_with_no_model_cost(make_assistant, held):
    svc, _ = make_assistant()
    a = svc.ask("Delete all cancelled orders", ADMIN)
    r = run_row(held, a.run_id)
    assert (r["status"], r["route"], r["tokens_in"], r["attempts_count"]) == ("refused",
                                                                              "unsafe", 0, 0)
    assert r["question"] == "Delete all cancelled orders" and r["est_cost"] == Decimal("0")


def test_a_clarification_is_recorded(make_assistant, held):
    svc, h = make_assistant()
    h.llm.say(RouteDecision, RouteDecision(route="ambiguous", question="Best customers?"))
    h.llm.say(ClarifyOut, ClarifyOut(question="Best by what?", options=["net revenue"]))
    r = run_row(held, svc.ask("Best customers?", ADMIN).run_id)
    assert r["status"] == "clarify" and r["route"] == "ambiguous" and r["attempts_count"] == 0


def test_a_guard_refusal_is_recorded_as_an_attempt(make_assistant, held):
    svc, h = make_assistant()
    h.llm.say(SQLDraft, SQLDraft(sql="SELECT * FROM gold.fact_orders"))
    a = svc.ask("anything", ADMIN)
    assert [(x[3], x[4]) for x in attempts(held, a.run_id)] == [("rejected", "guard")]
    assert run_row(held, a.run_id)["status"] == "refused"


def test_a_model_outage_is_recorded_as_a_failure(make_assistant, held):
    svc, h = make_assistant()

    def down(**kwargs):
        raise LLMError("OpenAI is unavailable")
    h.rt.llm = down
    a = svc.ask("How many orders?", ADMIN)
    r = run_row(held, a.run_id)
    assert a.status == "failed" and r["status"] == "failed" and r["attempts_count"] == 0


def test_an_empty_question_is_not_a_record(make_assistant, held):
    svc, _ = make_assistant()
    a = svc.ask("   ", ADMIN)
    assert a.status == "failed" and run_row(held, a.run_id) is None


# --- cost -------------------------------------------------------------------------------------
def test_cost_is_stored_when_prices_are_set(make_assistant, held):
    svc, h = make_assistant(price_input_per_1m=1.0, price_output_per_1m=2.0)
    h.llm.say(SQLDraft, SQLDraft(sql=ORDERS)).say(Narration, Narration(answer="ok"))
    a = svc.ask("How many orders?", ADMIN)
    expected = Decimal("0.000420")  # (300 x 1.0 + 60 x 2.0) / 1,000,000
    assert a.est_cost == expected and run_row(held, a.run_id)["est_cost"] == expected


def test_cost_is_null_not_zero_when_prices_are_not_set(make_assistant, held):
    svc, h = make_assistant(price_input_per_1m=None, price_output_per_1m=None,
                            price_input_per_1m_low=None, price_output_per_1m_low=None,
                            price_input_per_1m_medium=None, price_output_per_1m_medium=None)
    h.llm.say(SQLDraft, SQLDraft(sql=ORDERS)).say(Narration, Narration(answer="ok"))
    a = svc.ask("How many orders?", ADMIN)
    assert a.est_cost is None and run_row(held, a.run_id)["est_cost"] is None


# --- no personal data in the record -----------------------------------------------------------
def everything_stored(conn, run_id) -> str:
    runs = conn.execute("SELECT row_to_json(r)::text FROM ops.assistant_runs r WHERE run_id = %s",
                        (run_id,)).fetchone()[0]
    atts = [x[0] for x in conn.execute(
        "SELECT row_to_json(a)::text FROM ops.assistant_attempts a WHERE run_id = %s", (run_id,))]
    return runs + " ".join(atts)


def test_the_question_is_stored_without_phone_numbers_or_emails(make_assistant, held):
    svc, h = make_assistant()
    h.llm.say(RouteDecision, lambda t: RouteDecision(
        route="simple", question="orders for the customer on 9876543210 / priya@example.com"))
    h.llm.say(SQLDraft, SQLDraft(sql=ORDERS)).say(Narration, Narration(answer="ok"))
    a = svc.ask("how many orders did the customer on 9876543210 (priya@example.com) place?", ADMIN)
    stored = everything_stored(held, a.run_id)
    assert "9876543210" not in stored and "priya@example.com" not in stored
    r = run_row(held, a.run_id)
    assert "[phone]" in r["question"] and "[email]" in r["question"]
    assert "[phone]" in r["working_question"] and "[email]" in r["working_question"]


def test_personal_data_inside_the_generated_sql_is_scrubbed_too(make_assistant, held):
    svc, h = make_assistant()
    sql = ORDERS + " WHERE customer_id = '9876543210' OR restaurant_name = 'a@b.com'"
    h.llm.say(SQLDraft, SQLDraft(sql=sql)).say(Narration, Narration(answer="ok"))
    a = svc.ask("lookup", ADMIN)
    stored = everything_stored(held, a.run_id)
    assert "9876543210" not in stored and "a@b.com" not in stored


def test_database_error_text_is_scrubbed(make_assistant, held):
    svc, h = make_assistant(max_heal_attempts=0)
    h.llm.say(SQLDraft, SQLDraft(sql="SELECT nope_9876543210 FROM analytics.orders"))
    a = svc.ask("x", ADMIN)
    assert not PHONE.search(everything_stored(held, a.run_id))


def test_an_api_key_typed_into_a_question_is_never_stored(make_assistant, held):
    svc, _ = make_assistant()
    a = svc.ask("Delete everything, my key is sk-proj-AbCdEfGh1234567890xyz", ADMIN)
    assert "AbCdEfGh1234567890" not in everything_stored(held, a.run_id)
    assert "[api-key]" in run_row(held, a.run_id)["question"]


def test_a_very_long_question_is_cut_down_in_the_record(make_assistant, held):
    svc, _ = make_assistant()
    a = svc.ask("delete " + "x" * 5000, ADMIN)  # refused as too long, still audited
    assert len(run_row(held, a.run_id)["question"]) <= 1000


def test_nothing_in_a_whole_conversation_contains_a_phone_number_or_the_real_key(
        make_assistant, held):
    svc, h = make_assistant()
    h.llm.say(SQLDraft, SQLDraft(sql=ORDERS), SQLDraft(sql=ORDERS)).say(
        Narration, Narration(answer="a"), Narration(answer="b"))
    for q in ("orders for 9876543210?", "and for 8123456789?"):
        a = svc.ask(q, ADMIN, thread_id="conv")
        assert not PHONE.search(everything_stored(held, a.run_id))


def test_logs_written_while_answering_contain_no_personal_data(make_assistant, held, caplog):
    svc, h = make_assistant()
    h.llm.say(SQLDraft, SQLDraft(sql="SELECT nope FROM analytics.orders")).say(
        Repair, Repair(sql=ORDERS)).say(Narration, Narration(answer="ok"))
    with caplog.at_level(logging.DEBUG):
        svc.ask("orders for priya@example.com 9876543210", ADMIN)
    text = " ".join(r.getMessage() for r in caplog.records)
    assert "priya@example.com" not in text and "9876543210" not in text


# --- a broken audit table must not break answering --------------------------------------------
def test_if_the_audit_cannot_be_written_the_answer_still_comes_back_and_the_error_is_logged(
        make_assistant, held, caplog):
    svc, h = make_assistant()
    h.llm.say(SQLDraft, SQLDraft(sql=ORDERS)).say(Narration, Narration(answer="Still answered."))

    def broken():
        raise RuntimeError("the audit database is down; password=hunter2 9876543210")
    h.rt.open_service = broken
    with caplog.at_level(logging.ERROR):
        a = svc.ask("How many orders?", ADMIN)
    assert a.status == "answered" and a.answer == "Still answered." and a.audit_ok is False
    logged = " ".join(r.getMessage() for r in caplog.records)
    assert "AUDIT WRITE FAILED" in logged and "RuntimeError" in logged
    assert "hunter2" not in logged and "9876543210" not in logged  # only the error TYPE is logged


def test_audit_can_be_switched_off_and_then_writes_nothing(held):
    from swiggy_ai.agents.service import Assistant
    from swiggy_ai.semantic.loader import load_semantic
    from tests.phase08.conftest import Harness
    h = Harness(load_semantic())
    h.rt.open_service = lambda: held
    h.llm.say(SQLDraft, SQLDraft(sql=ORDERS)).say(Narration, Narration(answer="ok"))
    before = held.execute("SELECT count(*) FROM ops.assistant_runs").fetchone()[0]
    a = Assistant(h.rt, audit=False).ask("How many orders?", ADMIN)
    assert held.execute("SELECT count(*) FROM ops.assistant_runs").fetchone()[0] == before
    assert a.audit_ok is True and a.run_id


# --- the tables themselves --------------------------------------------------------------------
def test_the_assistant_role_cannot_read_the_audit_trail(held):
    for t in ("assistant_runs", "assistant_attempts"):
        assert held.execute("SELECT has_table_privilege('assistant_ro', %s, 'SELECT')",
                            (f"ops.{t}",)).fetchone()[0] is False


def test_deleting_a_run_removes_its_attempts(held):
    import uuid

    from swiggy_ai.ops.audit import AttemptRecord, RunRecord, record_run
    rid = uuid.uuid4()
    record_run(held, RunRecord(rid, "t", "u", "ALL", "q", "answered",
                               attempts=[AttemptRecord(1, 1, "writer", "SELECT 1", "ok")]))
    assert len(attempts(held, rid)) == 1
    held.execute("DELETE FROM ops.assistant_runs WHERE run_id = %s", (rid,))
    assert attempts(held, rid) == []


def test_a_run_and_its_attempts_are_written_together_or_not_at_all(held):
    import uuid

    from swiggy_ai.ops.audit import AttemptRecord, RunRecord, record_run
    rid = uuid.uuid4()
    bad = RunRecord(rid, "t", "u", "ALL", "q", "answered",
                    attempts=[AttemptRecord(1, 1, "writer", "SELECT 1", "ok"),
                              AttemptRecord(1, 1, "writer", "duplicate number", "ok")])
    with pytest.raises(Exception, match="duplicate key"):
        record_run(held, bad)
    assert run_row(held, rid) is None  # the run row was rolled back with the failed attempt rows


def test_jsonb_columns_hold_valid_json(make_assistant, held):
    svc, h = make_assistant()
    h.llm.say(SQLDraft, SQLDraft(sql=ORDERS)).say(Narration, Narration(answer="ok"))
    a = svc.ask("How many orders?", ADMIN)
    raw = held.execute("SELECT usage_by_step::text, models::text, checks::text, warnings::text"
                       " FROM ops.assistant_runs WHERE run_id = %s", (a.run_id,)).fetchone()
    assert all(json.loads(x) is not None for x in raw)
