"""Phase 13, part 6: the app-ready assistant reloads its catalog when the data version moves."""
import pytest

from swiggy_ai.agents import service
from swiggy_ai.agents.narrator import Narration
from swiggy_ai.agents.service import Assistant
from swiggy_ai.agents.sql_writer import SQLDraft
from swiggy_ai.db import connect
from swiggy_ai.ops import cache
from swiggy_ai.security.scope import AppUser
from tests.phase12.test_assistant_cache import NET, Keep

pytestmark = pytest.mark.db
ME = AppUser("admin", "Admin", "ALL")


@pytest.fixture
def conn():
    c = connect("etl")
    cache.ensure_cache_tables(c)
    yield c
    c.rollback()
    c.close()


@pytest.fixture
def make(harness, conn):
    def build(**kw):
        h = harness()
        h.rt.open_service = lambda: Keep(conn)
        h.llm.default(SQLDraft, SQLDraft(sql=NET))
        h.llm.default(Narration, Narration(answer="Here is the net revenue."))
        return h, Assistant(h.rt, audit=False, cache=False, **kw)
    return build


def test_a_new_data_version_makes_the_assistant_load_the_catalog_again(make, conn, monkeypatch):
    h, a = make(reload_catalog=True)
    loaded = []
    sentinel = object()
    monkeypatch.setattr(service, "load_semantic", lambda: loaded.append(1) or sentinel)
    original = h.rt.semantic
    a.ask("What is our net revenue?", ME)
    assert h.rt.semantic is original and loaded == []  # the first ask only notes the version
    a.ask("What is our net revenue again?", ME)
    assert loaded == []  # nothing changed, nothing reloaded
    cache.bump_data_version(conn)
    h.rt.semantic = original
    monkeypatch.setattr(service, "load_semantic", lambda: loaded.append(1) or original)
    a.ask("What is our net revenue now?", ME)
    assert loaded == [1]
    a.ask("And once more?", ME)
    assert loaded == [1]  # reloaded once per change, not on every question


def test_a_catalog_that_cannot_be_loaded_is_not_allowed_to_break_answering(make, conn,
                                                                           monkeypatch):
    h, a = make(reload_catalog=True)
    a.ask("What is our net revenue?", ME)
    cache.bump_data_version(conn)

    def broken():
        raise ValueError("catalog.yml is invalid")
    monkeypatch.setattr(service, "load_semantic", broken)
    original = h.rt.semantic
    assert a.ask("What is our net revenue?", ME).status == "answered"
    assert h.rt.semantic is original  # kept the one that works


def test_without_the_flag_nothing_is_reloaded(make, conn, monkeypatch):
    h, a = make()
    monkeypatch.setattr(service, "load_semantic", lambda: pytest.fail("must not reload"))
    a.ask("What is our net revenue?", ME)
    cache.bump_data_version(conn)
    assert a.ask("What is our net revenue?", ME).status == "answered"


def test_for_app_turns_on_audit_cache_and_reloading(harness, conn):
    h = harness()
    h.rt.open_service = lambda: Keep(conn)
    a = Assistant.for_app(h.rt)
    assert a.audit and a.reload_catalog and a.cache is h.rt.settings.answer_cache
    off = h.rt.settings.model_copy(update={"answer_cache": False})
    h.rt.settings = off
    assert Assistant.for_app(h.rt).cache is False
