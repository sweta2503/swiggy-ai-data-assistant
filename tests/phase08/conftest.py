"""Helpers for the agent tests: a Runtime with a scripted model and the real database."""
import time

import pytest

from swiggy_ai.agents.graph import build_graph
from swiggy_ai.agents.runtime import Runtime
from swiggy_ai.agents.state import AgentState, fresh_turn
from swiggy_ai.config import get_settings
from swiggy_ai.db import connect
from swiggy_ai.security.scope import assistant_connection
from swiggy_ai.semantic.loader import load_semantic
from tests.phase08.fakes import FakeLLM


@pytest.fixture(scope="session", autouse=True)
def security_ready():
    """The agent runs as assistant_ro, so the security setup must be in place."""
    from swiggy_ai.security.setup import apply_security
    apply_security()


@pytest.fixture(scope="session")
def semantic():
    return load_semantic()


@pytest.fixture
def service():
    c = connect("etl", autocommit=True)
    if c.execute("SELECT count(*) FROM gold.fact_orders").fetchone()[0] == 0:
        pytest.skip("the dev database has no data; run the pipeline first")
    yield c
    c.close()


class Harness:
    """Builds a graph around a fake model and runs questions through it."""

    def __init__(self, semantic, **settings):
        self.llm = FakeLLM()
        base = get_settings().model_copy(update={"demo_fault_injection": False, **settings})
        self.rt = Runtime(settings=base, semantic=semantic, llm=self.llm,
                          open_assistant=assistant_connection,
                          open_service=lambda: connect("etl", autocommit=True),
                          clock=time.time, health_check=lambda: [])
        self.graph = build_graph(self.rt)

    def ask(self, question: str, scope: str = "ALL", thread: str = "t") -> AgentState:
        turn = fresh_turn(question, thread_id=thread, user_id="tester", allowed_cities=scope,
                          started_at=time.time())
        out = self.graph.invoke(turn, {"configurable": {"thread_id": thread}})
        return AgentState(**out)


@pytest.fixture
def harness(semantic, service):
    return lambda **settings: Harness(semantic, **settings)
