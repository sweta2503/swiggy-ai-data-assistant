"""Phase 9 helpers: an assistant whose audit writes go into a transaction we roll back."""
import pytest

from swiggy_ai.agents.service import Assistant
from swiggy_ai.db import connect
from swiggy_ai.ops.audit import ensure_audit_tables
from swiggy_ai.semantic.loader import load_semantic
from tests.phase08.conftest import Harness


@pytest.fixture(scope="session", autouse=True)
def ready():
    from swiggy_ai.security.setup import apply_security
    apply_security()
    with connect("etl", autocommit=True) as c:  # the audit tables, committed once
        ensure_audit_tables(c)


class Held:
    """Looks like a connection but ignores close(), so the test keeps its transaction open."""

    def __init__(self, conn):
        self._c = conn
        # Open the transaction NOW. Otherwise a `with conn.transaction()` inside the code under
        # test would be the outermost one and would COMMIT, leaking rows into the real database.
        conn.execute("SELECT 1")

    def __getattr__(self, name):
        return getattr(self._c, name)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def close(self):
        pass


@pytest.fixture
def held():
    c = connect("etl")
    yield Held(c)
    c.rollback()
    c.close()


@pytest.fixture
def make_assistant(held):
    """-> (assistant, harness). The audit rows go to `held` and vanish when the test ends."""
    sem = load_semantic()

    def build(**settings):
        h = Harness(sem, **settings)
        h.rt.open_service = lambda: held
        return Assistant(h.rt), h
    return build
