"""Phase 9, part 3: `make report` tells the truth about the audit trail."""
import uuid
from decimal import Decimal

import pytest

from swiggy_ai.ops.audit import AttemptRecord as A
from swiggy_ai.ops.audit import RunRecord, record_run
from swiggy_ai.ops.report import collect, render

pytestmark = pytest.mark.db


def add(conn, user, status, *, route="simple", healed=False, latency=1000, tin=100, tout=10,
        cost=None, attempts=(), fault=False, days_ago=0, question="q"):
    rid = uuid.uuid4()
    record_run(conn, RunRecord(rid, "t", user, "ALL", question, status, route=route, healed=healed,
                               latency_ms=latency, tokens_in=tin, tokens_out=tout,
                               est_cost=None if cost is None else Decimal(str(cost)),
                               fault_injected=fault, attempts=list(attempts)))
    if days_ago:
        conn.execute("UPDATE ops.assistant_runs SET ts = now() - make_interval(days => %s)"
                     " WHERE run_id = %s", (days_ago, rid))
    return rid


@pytest.fixture
def trail(held):
    held.execute("DELETE FROM ops.assistant_runs")  # start empty; rolled back afterwards
    return held


@pytest.fixture
def seeded(trail):
    c = trail
    add(c, "anita", "answered", latency=2000, tin=1000, tout=100, cost=0.01,
        attempts=[A(1, 1, "writer", "SELECT 1 /* fast */", "ok", latency_ms=300)])
    add(c, "anita", "answered", healed=True, latency=5000, tin=2000, tout=200, cost=0.03,
        fault=True,
        attempts=[A(1, 1, "fault", "SELECT bad", "error", "undefined_column", "no col", 40),
                  A(2, 1, "healer", "SELECT SUM(net_revenue) FROM analytics.orders", "ok",
                    latency_ms=2500)])
    add(c, "bala", "answered", route="multi_step", latency=9000, tin=6000, tout=900, cost=0.05,
        attempts=[A(1, 1, "writer", "SELECT a", "ok", latency_ms=900),
                  A(2, 2, "writer", "SELECT b", "ok", latency_ms=100),
                  A(3, 3, "writer", "SELECT c", "ok", latency_ms=1800)])
    add(c, "bala", "failed", latency=3000, tin=500, tout=50, cost=None,
        attempts=[A(1, 1, "writer", "SELEC", "rejected", "syntax", "parse", 5),
                  A(2, 1, "healer", "SELEC2", "rejected", "syntax", "parse", 6)])
    add(c, "bala", "refused", route="unsafe", latency=400, tin=0, tout=0, cost=0,
        attempts=[A(1, 1, "writer", "DELETE FROM x", "rejected", "guard", "not_select", 1)])
    add(c, "anita", "clarify", route="ambiguous", latency=800, tin=300, tout=40, cost=0.001)
    add(c, "anita", "answered", latency=1000, days_ago=10)  # too old: must not be counted
    return c


# --- the numbers ------------------------------------------------------------------------------
def test_counts_and_rates(seeded):
    r = collect(seeded, 7)
    assert r.total == 6 and r.users == 2
    assert r.by_status == {"answered": 3, "failed": 1, "refused": 1, "clarify": 1}
    assert r.success_rate == pytest.approx(3 / 4)  # answered / (answered + failed)
    assert r.healed == 1 and r.heal_rate == pytest.approx(1 / 3)  # of the answered ones
    assert r.by_route == {"simple": 3, "multi_step": 1, "unsafe": 1, "ambiguous": 1}


def test_only_the_requested_window_is_counted(seeded):
    assert collect(seeded, 7).total == 6
    assert collect(seeded, 30).total == 7
    assert collect(seeded, 30).by_status["answered"] == 4
    assert collect(seeded, 1).total == 6  # everything seeded "today"


def test_guard_rejections_and_top_problems(seeded):
    r = collect(seeded, 7)
    assert r.guard_rejections == 1
    assert r.error_classes[0] == ("syntax", 2)
    assert set(r.error_classes) == {("syntax", 2), ("undefined_column", 1), ("guard", 1)}
    assert all(c != "ok" for c, _ in r.error_classes)  # successes are not problems


def test_slowest_queries_are_listed_slowest_first(seeded):
    r = collect(seeded, 7)
    assert [q[0] for q in r.slowest_queries] == [2500, 1800, 900, 300, 100]
    assert r.slowest_queries[0][1] == "anita" and "net_revenue" in r.slowest_queries[0][3]


def test_tokens_and_speed(seeded):
    r = collect(seeded, 7)
    assert (r.tokens_in, r.tokens_out) == (9800, 1290)
    assert r.avg_latency_ms == pytest.approx((2000 + 5000 + 9000 + 3000 + 400 + 800) / 6)
    assert r.p50_latency_ms == pytest.approx(2500)  # the middle of 400 800 2000 3000 5000 9000
    assert 5000 < r.p95_latency_ms <= 9000


def test_cost_is_totalled_only_over_questions_whose_cost_is_known(seeded):
    r = collect(seeded, 7)
    assert r.model_runs == 5  # the refusal used no model, so it is neither here nor there
    assert r.runs_with_cost == 4  # one failed run has no price information
    assert r.cost_total == Decimal("0.091000") and r.cost_avg == Decimal("0.022750")


def test_demo_runs_are_counted_separately(seeded):
    assert collect(seeded, 7).fault_runs == 1


# --- the text ---------------------------------------------------------------------------------
def test_the_report_reads_clearly(seeded):
    text = render(collect(seeded, 7))
    for expected in ("last 7 days", "Questions         6  (from 2 users)", "answered        3",
                     "Success rate      75%", "Heal rate         33%",
                     "Guard rejections  1", "Top problems", "syntax", "Slowest queries",
                     "2,500", "Cost (USD)", "$0.0910", "4 of 5", "Demo faults"):
        assert expected.replace(",", "") in text.replace(",", ""), expected


def test_an_empty_trail_says_so_instead_of_dividing_by_zero(trail):
    assert render(collect(trail, 7)).endswith("No questions in the last 7 days.")


def test_when_no_prices_were_ever_set_the_report_says_cost_is_not_computed(trail):
    add(trail, "u", "answered", cost=None)
    text = render(collect(trail, 7))
    assert "cost not computed (prices not set)" in text and "$" not in text


def test_a_trail_with_only_refusals_has_no_success_rate_to_report(trail):
    add(trail, "u", "refused", route="unsafe", tin=0, tout=0, cost=0)
    r = collect(trail, 7)
    assert r.success_rate is None and r.heal_rate is None
    text = render(r)
    assert "Success rate      n/a" in text
    assert "$0 (no question used the model)" in text  # not "cost not computed"


def test_refusals_alone_do_not_make_unpriced_questions_look_priced(trail):
    add(trail, "u", "refused", route="unsafe", tin=0, tout=0, cost=0)  # free
    add(trail, "u", "answered", cost=None)  # used the model, price unknown
    text = render(collect(trail, 7))
    assert "cost not computed (prices not set)" in text and "$" not in text


def test_the_report_never_prints_a_whole_sql_statement_on_one_unreadable_line(trail):
    add(trail, "u", "answered",
        attempts=[A(1, 1, "writer", "SELECT\n  a,\n  b\nFROM analytics.orders " + "x" * 300, "ok",
                    latency_ms=50)])
    line = [x for x in render(collect(trail, 7)).splitlines() if "ms" in x and "SELECT" in x][0]
    assert "\n" not in line and len(line) < 130
