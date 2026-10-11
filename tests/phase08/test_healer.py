"""Increment 08b: the self-healing loop, and the demo-only fault injection that shows it off.

What the healer may and may not do is the point: it repairs ordinary SQL mistakes, makes the
smallest change, gives up at its limit or when it repeats itself, gets ONE try for a timeout, and
is never allowed near a guard refusal or a permission error.
"""
import psycopg
import pytest

from swiggy_ai.agents.faults import load_faults
from swiggy_ai.agents.healer import Repair
from swiggy_ai.agents.narrator import Narration
from swiggy_ai.agents.sql_writer import SQLDraft

pytestmark = pytest.mark.db
GOOD = ("SELECT SUM(order_value - discount) FILTER (WHERE status = 'delivered') AS net_revenue "
        "FROM analytics.orders")
BAD_COLUMN = GOOD.replace("order_value", "total_amount")
SLOW = ("SELECT count(*) FROM analytics.orders a CROSS JOIN analytics.orders b "
        "CROSS JOIN analytics.orders c")


def says(user_input):
    return Narration(answer="Here is the net revenue you asked for.")


def healed_harness(harness, *repairs, writer=BAD_COLUMN, **settings):
    h = harness(**settings)
    h.llm.say(SQLDraft, SQLDraft(sql=writer))
    h.llm.say(Repair, *[Repair(sql=r, changed="fixed it") for r in repairs])
    h.llm.say(Narration, says)
    return h


# --- a repair that works ----------------------------------------------------------------------
def test_a_wrong_column_is_repaired_and_the_answer_comes_back(harness, service):
    truth = float(service.execute("SELECT sum(net_revenue) FROM gold.fact_orders").fetchone()[0])
    h = healed_harness(harness, GOOD)
    s = h.ask("What is our total net revenue?")
    assert s.status == "answered" and s.results[0].rows == [[truth]]
    assert [(a.source, a.status, a.error_class) for a in s.attempts] == [
        ("writer", "error", "undefined_column"), ("healer", "ok", "")]
    assert s.results[0].healed == 1
    assert any("repaired automatically" in w for w in s.warnings)
    assert "fixed it" in s.results[0].assumptions


def test_the_healer_is_given_exactly_what_it_needs_and_no_more(harness):
    h = healed_harness(harness, GOOD)
    h.ask("What is our total net revenue?")
    call = next(c for c in h.llm.calls if c.schema == "Repair")
    text = call.user_input
    assert "SUM(total_amount - discount)" in text  # the failed query (as the guard wrote it)
    assert 'column "total_amount" does not exist' in text  # the database's own words
    assert "Error class: undefined_column" in text
    assert "net_revenue numeric" in text and "order_value numeric" in text  # the real columns
    assert "SMALLEST change" in call.instructions and "DATEADD" in call.instructions
    assert call.tier == "medium"
    assert "gold." not in text and "silver." not in text


@pytest.mark.parametrize("bad, error_class", [
    ("SELEC SUM(order_value) FROM analytics.orders", "syntax"),
    ("SELECT SUM(order_valu) FROM analytics.orders", "undefined_column"),
    ("SELECT SUM(order_value) FROM analytics.orderz", "forbidden"),
    ("SELECT city, order_id FROM analytics.orders GROUP BY city", "grouping_error"),
    ("SELECT GETDATE() FROM analytics.orders", "datatype_mismatch"),
    ("SELECT order_id FROM analytics.orders o JOIN analytics.order_items i USING (order_id)"
     " WHERE city = 'x' AND restaurant_id = 'y'", "ambiguous_column"),
])
def test_each_kind_of_ordinary_mistake_is_repaired(harness, bad, error_class):
    h = healed_harness(harness, GOOD, writer=bad)
    s = h.ask("What is our total net revenue?")
    if error_class == "forbidden":  # an unknown view is a guard refusal, not a typo
        assert s.status == "refused" and h.llm.count(Repair) == 0
    else:
        assert s.status == "answered" and h.llm.count(Repair) == 1
        assert s.attempts[0].error_class == error_class


# --- where it must stop -----------------------------------------------------------------------
def test_it_gives_up_after_the_heal_limit(harness):
    broken = [f"SELECT a{i} FROM analytics.orders" for i in range(1, 5)]
    h = healed_harness(harness, *broken, max_heal_attempts=3)
    s = h.ask("net revenue")
    assert h.llm.count(Repair) == 3 and len(s.attempts) == 4  # 1 original + 3 repairs
    assert s.status == "failed" and "I could not answer" in s.answer
    assert h.llm.count(Narration) == 0


@pytest.mark.parametrize("limit", [0, 1, 2])
def test_the_limit_is_configurable(harness, limit):
    broken = [f"SELECT x{i} FROM analytics.orders" for i in range(5)]
    h = healed_harness(harness, *broken, max_heal_attempts=limit)
    h.ask("net revenue")
    assert h.llm.count(Repair) == limit


def test_it_stops_when_a_repair_repeats_an_earlier_query(harness):
    h = healed_harness(harness, BAD_COLUMN, GOOD, max_heal_attempts=3)  # the first repair repeats
    s = h.ask("net revenue")
    assert h.llm.count(Repair) == 1 and s.status == "failed"
    assert "already failed" in s.answer


def test_it_also_recognises_a_repeat_that_is_only_formatted_differently(harness):
    spaced = "select   sum(total_amount)  filter (where status='delivered')  from analytics.orders"
    h = healed_harness(harness, spaced, writer="SELECT SUM(total_amount) FILTER (WHERE status = "
                                               "'delivered') FROM analytics.orders")
    s = h.ask("net revenue")
    assert h.llm.count(Repair) == 1 and "already failed" in s.answer


def test_guard_refusals_are_never_sent_to_the_healer(harness, service):
    before = service.execute("SELECT count(*) FROM gold.fact_orders").fetchone()[0]
    h = healed_harness(harness, GOOD, writer="DELETE FROM analytics.orders")
    s = h.ask("net revenue")
    assert s.status == "refused" and h.llm.count(Repair) == 0
    assert service.execute("SELECT count(*) FROM gold.fact_orders").fetchone()[0] == before


@pytest.mark.parametrize("unsafe", ["SELECT pg_sleep(5)", "SELECT * FROM gold.fact_orders",
                                    "DROP VIEW analytics.orders"])
def test_a_repair_that_is_unsafe_is_refused_and_ends_the_loop(harness, unsafe):
    h = healed_harness(harness, unsafe, GOOD)  # the second repair must never be asked for
    s = h.ask("net revenue")
    assert h.llm.count(Repair) == 1 and s.status == "refused"
    assert [a.status for a in s.attempts] == ["error", "rejected"]


def test_permission_errors_are_not_healed(harness):
    class Denied:
        def execute(self, *args, **kwargs):
            raise psycopg.errors.InsufficientPrivilege("permission denied for table orders")

        def close(self):
            pass
    h = healed_harness(harness, GOOD, writer=GOOD)
    h.rt.open_assistant = lambda scope: Denied()
    s = h.ask("net revenue")
    assert s.status == "failed" and s.attempts[0].error_class == "permission_denied"
    assert h.llm.count(Repair) == 0 and "access was denied" in s.answer


def test_an_unclassified_error_is_not_healed(harness):
    h = healed_harness(harness, GOOD, writer="SELECT 1 / 0")
    s = h.ask("net revenue")
    assert s.attempts[0].error_class == "other" and h.llm.count(Repair) == 0


# --- timeouts: one attempt, with a hint to do less work ---------------------------------------
def test_a_timeout_gets_one_repair_with_a_hint_and_it_can_succeed(harness):
    h = healed_harness(harness, GOOD, writer=SLOW, query_timeout_ms=500, max_plan_cost=1e18)
    s = h.ask("net revenue")
    assert s.status == "answered"
    assert [(a.source, a.error_class) for a in s.attempts] == [("writer", "timeout"),
                                                                ("healer", "")]
    call = next(c for c in h.llm.calls if c.schema == "Repair")
    assert "This was a timeout. Do less work" in call.user_input


def test_a_second_timeout_is_final(harness):
    h = healed_harness(harness, SLOW + " WHERE a.city = 'Pune'", SLOW, writer=SLOW,
                       query_timeout_ms=500, max_plan_cost=1e18, max_heal_attempts=3)
    s = h.ask("net revenue")
    assert h.llm.count(Repair) == 1 and s.status == "failed"
    assert "took too long" in s.answer


# --- the repair is itself checked -------------------------------------------------------------
def test_a_repair_goes_back_through_the_guard_and_the_row_cap(harness):
    h = healed_harness(harness, "SELECT order_id FROM analytics.orders", writer=BAD_COLUMN,
                       max_rows=20)
    s = h.ask("list orders")
    assert s.final_sql.endswith("LIMIT 20") and len(s.results[0].rows) == 20


def test_a_repair_that_is_itself_broken_can_be_repaired_again(harness):
    h = healed_harness(harness, "SELECT order_valu FROM analytics.orders", GOOD)
    s = h.ask("net revenue")
    assert s.status == "answered" and h.llm.count(Repair) == 2
    assert [a.status for a in s.attempts] == ["error", "error", "ok"]
    assert s.results[0].healed == 2


# --- demo fault injection ---------------------------------------------------------------------
def test_fault_injection_is_off_unless_asked_for(harness):
    h = harness()
    h.llm.say(SQLDraft, SQLDraft(sql=GOOD)).say(Narration, says)
    s = h.ask("net revenue")
    assert not s.fault_injected and [a.source for a in s.attempts] == ["writer"]


def test_an_injected_fault_is_flagged_everywhere_and_then_healed(harness, service):
    truth = float(service.execute("SELECT sum(net_revenue) FROM gold.fact_orders").fetchone()[0])
    h = healed_harness(harness, GOOD, writer=GOOD, demo_fault_injection=True)
    s = h.ask("What is our total net revenue?")

    assert s.fault_injected
    assert [(a.source, a.status) for a in s.attempts] == [("fault", "error"), ("healer", "ok")]
    assert "total_amount" in s.attempts[0].sql  # the model's own query, broken on purpose
    assert any(w.startswith("Fault injected for the demo") for w in s.warnings)
    assert s.status == "answered" and s.results[0].rows == [[truth]]


def test_only_the_first_query_is_ever_faulted(harness):
    h = healed_harness(harness, "SELECT nope FROM analytics.orders", GOOD, writer=GOOD,
                       demo_fault_injection=True)
    s = h.ask("net revenue")
    assert [a.source for a in s.attempts] == ["fault", "healer", "healer"]


@pytest.mark.parametrize("fault", [f["name"] for f in load_faults()])
def test_every_fault_in_the_file_produces_an_error_the_healer_can_fix(harness, fault):
    h = healed_harness(harness, GOOD, writer=GOOD, demo_fault_injection=True,
                       demo_fault_name=fault)
    s = h.ask("net revenue")
    assert s.attempts[0].source == "fault" and s.attempts[0].status in ("error", "rejected")
    assert s.attempts[0].error_class in ("undefined_column", "datatype_mismatch",
                                         "grouping_error")
    assert s.status == "answered"


def test_the_fault_falls_back_to_a_fixed_query_when_the_model_query_has_nothing_to_break(harness):
    from swiggy_ai.agents.faults import inject_fault
    sql, fault = inject_fault("SELECT 1", "wrong_column")
    assert "total_amount" in sql and fault["name"] == "wrong_column"
    broken, _ = inject_fault(GOOD, "wrong_column")
    assert "total_amount" in broken and "order_value" not in broken


def test_the_injection_is_logged_as_a_warning(harness, caplog):
    import logging
    h = healed_harness(harness, GOOD, writer=GOOD, demo_fault_injection=True)
    with caplog.at_level(logging.WARNING, logger="agents.sql_writer"):
        h.ask("net revenue")
    assert any("fault injected" in r.message for r in caplog.records)
