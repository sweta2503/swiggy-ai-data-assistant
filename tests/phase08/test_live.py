"""Live tests: the real model, the real database. Skipped without OPENAI_API_KEY.

They are the honest check that the prompts and the context really let a small, cheap model write
correct SQL. Each answer is compared with a separately written query.
"""
import time

import pytest

from swiggy_ai.agents.graph import build_graph
from swiggy_ai.agents.runtime import Runtime
from swiggy_ai.agents.state import AgentState, fresh_turn
from swiggy_ai.config import get_settings
from swiggy_ai.db import connect

pytestmark = [pytest.mark.live, pytest.mark.db]


@pytest.fixture(scope="module")
def live():
    rt = Runtime.create(get_settings().model_copy(update={"demo_fault_injection": False}))
    rt.health_check = lambda: []
    return rt, build_graph(rt)


@pytest.fixture
def service():
    c = connect("etl", autocommit=True)
    if c.execute("SELECT count(*) FROM gold.fact_orders").fetchone()[0] == 0:
        pytest.skip("no data in the dev database")
    yield c
    c.close()


def ask(live, question, scope="ALL", thread=None) -> AgentState:
    _, graph = live
    thread = thread or f"live-{time.time()}"
    out = graph.invoke(fresh_turn(question, thread_id=thread, user_id="live",
                                  allowed_cities=scope, started_at=time.time()),
                       {"configurable": {"thread_id": thread}})
    return AgentState(**out)


def test_total_net_revenue(live, service):
    truth = float(service.execute("SELECT sum(net_revenue) FROM gold.fact_orders").fetchone()[0])
    s = ask(live, "What is our total net revenue?")
    print(f"\n  answer: {s.answer}\n  sql: {s.final_sql}")
    print(f"  tokens: {s.usage.tokens_in}+{s.usage.tokens_out}")
    assert s.status == "answered"
    assert s.results[0].rows[0][0] == pytest.approx(truth)


def test_a_scoped_user_gets_only_their_city(live, service):
    truth = float(service.execute(
        "SELECT sum(f.net_revenue) FROM gold.fact_orders f JOIN gold.dim_city c USING (city_sk)"
        " WHERE c.city_name = 'Pune'").fetchone()[0])
    s = ask(live, "What is our total net revenue?", scope="Pune")
    assert s.status == "answered" and s.results[0].rows[0][0] == pytest.approx(truth)


def test_relative_dates_use_the_data_not_the_clock(live, service):
    truth = service.execute(
        "SELECT count(*) FROM analytics.orders WHERE order_date >= date_trunc('week',"
        " (SELECT today FROM analytics.data_as_of)) - INTERVAL '7 days' AND order_date <"
        " date_trunc('week', (SELECT today FROM analytics.data_as_of))").fetchone()[0]
    s = ask(live, "How many orders did we get last week?")
    print(f"\n  {s.answer}\n  {s.final_sql}")
    assert s.status == "answered" and s.results[0].rows[0][0] == truth > 0
    assert "current_date" not in s.final_sql.lower() and "now()" not in s.final_sql.lower()


def test_a_demo_fault_is_repaired_by_the_real_model(service):
    truth = float(service.execute("SELECT sum(net_revenue) FROM gold.fact_orders").fetchone()[0])
    rt = Runtime.create(get_settings().model_copy(update={"demo_fault_injection": True}))
    rt.health_check = lambda: []
    s = ask((rt, build_graph(rt)), "What is our total net revenue?")
    for a in s.attempts:
        print(f"\n  attempt {a.no} [{a.source}] {a.status} {a.error_class}: {a.error_text[:70]}")
    print(f"\n  warnings: {s.warnings}\n  answer: {s.answer}")
    assert s.fault_injected and s.attempts[0].source == "fault"
    assert s.status == "answered" and s.results[0].rows[0][0] == pytest.approx(truth)
    assert s.results[0].healed >= 1
