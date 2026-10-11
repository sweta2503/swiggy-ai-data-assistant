"""Phase 12, part 2: the assistant with the cache on (scripted model, real database).

The assistant's own connection (cache and audit) is ONE transaction that is rolled back at the end,
so nothing from these tests stays in ops.answer_cache or the audit trail.
"""
import pytest

from swiggy_ai.agents.clarifier import ClarifyOut
from swiggy_ai.agents.narrator import Narration
from swiggy_ai.agents.router import RouteDecision
from swiggy_ai.agents.service import Assistant
from swiggy_ai.agents.sql_writer import SQLDraft
from swiggy_ai.db import connect
from swiggy_ai.ops import cache
from swiggy_ai.ops.report import collect, render
from swiggy_ai.security.scope import AppUser

pytestmark = pytest.mark.db
NET = ("SELECT SUM(order_value - discount) FILTER (WHERE status = 'delivered') AS net_revenue "
       "FROM analytics.orders")
ADMIN = AppUser("admin", "Admin", "ALL")
PUNE = AppUser("pune", "Pune manager", "Pune")


class Keep:
    """Hands out the test's transaction and does not close it."""

    def __init__(self, conn):
        self.conn = conn

    def __enter__(self):
        return self.conn

    def __exit__(self, *a):
        return False


@pytest.fixture
def conn():
    c = connect("etl")
    cache.ensure_cache_tables(c)
    c.execute("DELETE FROM ops.answer_cache")  # rolled back: the real cache is untouched
    yield c
    c.rollback()
    c.close()


@pytest.fixture
def make(harness, conn):
    def build(cache_on=True, audit=False, **settings):
        h = harness(**settings)
        h.rt.open_service = lambda: Keep(conn)
        h.llm.default(SQLDraft, SQLDraft(sql=NET))
        h.llm.default(Narration, Narration(answer="Here is the net revenue you asked for."))
        return h, Assistant(h.rt, audit=audit, cache=cache_on)
    return build


def model_calls(h):
    return len(h.llm.calls)


def test_the_second_ask_is_served_from_the_cache_without_calling_the_model(make):
    h, a = make()
    first = a.ask("What is our net revenue?", ADMIN)
    used = model_calls(h)
    assert first.status == "answered" and not first.cache_hit and used > 0

    again = a.ask("what is our NET revenue", ADMIN)  # typed differently, same question
    assert again.cache_hit and again.status == "answered"
    assert model_calls(h) == used  # nothing was asked of the model
    assert again.answer == first.answer and again.sql == first.sql
    assert again.steps[0].rows == first.steps[0].rows  # the data came along with the answer
    assert again.usage.tokens_in == 0 and again.usage.calls == 0
    assert again.cached_at and again.run_id != first.run_id
    assert [(c.name, c.passed) for c in again.checks] == [(c.name, c.passed) for c in first.checks]


def test_a_different_scope_never_gets_another_scopes_answer(make):
    h, a = make()
    a.ask("What is our net revenue?", ADMIN)
    used = model_calls(h)
    pune = a.ask("What is our net revenue?", PUNE)
    assert not pune.cache_hit and model_calls(h) > used
    assert a.ask("What is our net revenue?", PUNE).cache_hit  # but Pune's own is cached
    assert a.ask("What is our net revenue?", ADMIN).cache_hit
    assert pune.steps[0].rows != a.ask("What is our net revenue?", ADMIN).steps[0].rows


def test_new_data_retires_cached_answers(make, conn):
    h, a = make()
    a.ask("What is our net revenue?", ADMIN)
    assert a.ask("What is our net revenue?", ADMIN).cache_hit
    cache.bump_data_version(conn)
    after = a.ask("What is our net revenue?", ADMIN)
    assert not after.cache_hit
    assert a.ask("What is our net revenue?", ADMIN).cache_hit  # cached again, under the new version


def test_fresh_skips_the_lookup_but_replaces_the_cached_answer(make):
    h, a = make()
    a.ask("What is our net revenue?", ADMIN)
    h.llm.say(Narration, Narration(answer="A newer wording of the net revenue."))
    forced = a.ask("What is our net revenue?", ADMIN, fresh=True)
    assert not forced.cache_hit and forced.answer == "A newer wording of the net revenue."
    assert a.ask("What is our net revenue?", ADMIN).answer == forced.answer


def test_the_cache_is_off_unless_asked_for(make, conn):
    h, a = make(cache_on=False)
    a.ask("What is our net revenue?", ADMIN)
    second = a.ask("What is our net revenue?", ADMIN)
    assert not second.cache_hit
    assert conn.execute("SELECT count(*) FROM ops.answer_cache").fetchone()[0] == 0


def test_only_the_first_question_of_a_conversation_is_cached_or_looked_up(make, conn):
    h, a = make()
    a.ask("What is our net revenue?", ADMIN)  # cached
    assert conn.execute("SELECT count(*) FROM ops.answer_cache").fetchone()[0] == 1
    h.llm.default(RouteDecision, RouteDecision(route="simple", question="net revenue"))
    a.ask("How many orders?", ADMIN, thread_id="t1")  # first turn of t1: cached
    count = conn.execute("SELECT count(*) FROM ops.answer_cache").fetchone()[0]
    follow = a.ask("What is our net revenue?", ADMIN, thread_id="t1")  # a SECOND turn
    assert not follow.cache_hit  # even though the same words are cached for a first turn
    assert conn.execute("SELECT count(*) FROM ops.answer_cache").fetchone()[0] == count


def test_a_cache_hit_still_counts_as_a_turn_for_follow_ups(make):
    h, a = make()
    a.ask("What is our net revenue?", ADMIN)  # stored
    a.ask("What is our net revenue?", ADMIN, thread_id="B")  # a hit in a new thread
    router = [c for c in h.llm.calls if c.schema == "RouteDecision"]
    n = len(router)
    a.ask("And in Pune?", ADMIN, thread_id="B")  # the follow-up must see the earlier question
    last = [c for c in h.llm.calls if c.schema == "RouteDecision"][n]
    assert "what is our net revenue" in last.user_input.lower()


def stored(conn):
    return [r[0] for r in conn.execute("SELECT question_norm FROM ops.answer_cache").fetchall()]


def test_refusals_and_clarifying_questions_are_not_cached_but_real_answers_are(make, conn):
    h, a = make()
    assert a.ask("Drop the orders table please", ADMIN).status == "refused"  # the code screen
    h.llm.say(RouteDecision, RouteDecision(route="ambiguous", question="How are we doing?"))
    h.llm.say(ClarifyOut, ClarifyOut(question="Best by what?", options=["net revenue"]))
    assert a.ask("How are we doing?", ADMIN).status == "clarify"
    assert stored(conn) == []  # neither was kept

    assert a.ask("What is our net revenue?", ADMIN).status == "answered"
    assert stored(conn) == ["what is our net revenue"]  # the control: a real answer is kept


def test_an_answer_with_a_warning_is_not_cached(make, conn):
    h, a = make()
    h.rt.health_check = lambda: ["data quality warning: gold check failed"]
    first = a.ask("What is our net revenue?", ADMIN)
    assert first.warnings and first.status == "answered"
    assert conn.execute("SELECT count(*) FROM ops.answer_cache").fetchone()[0] == 0


def test_a_question_that_looks_personal_is_never_stored(make, conn):
    h, a = make()
    a.ask("What is our net revenue? my phone is 9876543210", ADMIN)
    a.ask("Net revenue for customer priya@example.com", ADMIN)
    assert stored(conn) == []
    a.ask("What is our net revenue?", ADMIN)
    assert stored(conn) == ["what is our net revenue"]  # the control


def test_a_broken_cache_never_breaks_answering(make, monkeypatch):
    h, a = make()

    def boom(*args, **kw):
        raise RuntimeError("cache table is gone")
    monkeypatch.setattr(cache, "lookup", boom)
    monkeypatch.setattr(cache, "store", boom)
    out = a.ask("What is our net revenue?", ADMIN)
    assert out.status == "answered" and not out.cache_hit


def test_cache_hits_are_audited_and_reported(make, conn):
    h, a = make(audit=True)
    a.ask("What is our net revenue?", ADMIN)
    hit = a.ask("What is our net revenue?", ADMIN)
    assert hit.cache_hit and hit.audit_ok
    rows = conn.execute("SELECT cache_hit, tokens_in FROM ops.assistant_runs WHERE run_id = %s",
                        (hit.run_id,)).fetchall()
    assert rows == [(True, 0)]
    report = collect(conn)
    assert report.cache_hits >= 1
    assert "Cache hits" in render(report)
