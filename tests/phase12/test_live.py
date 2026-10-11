"""Live: the real model, asked the same question twice. Skipped without OPENAI_API_KEY.
The cache and audit rows go into one transaction that is rolled back."""
import pytest

from swiggy_ai.agents.runtime import Runtime
from swiggy_ai.agents.service import Assistant
from swiggy_ai.config import get_settings
from swiggy_ai.db import connect
from swiggy_ai.ops import cache
from swiggy_ai.security.scope import AppUser
from tests.phase12.test_assistant_cache import Keep

pytestmark = [pytest.mark.live, pytest.mark.db]


def test_the_second_identical_question_is_a_cache_hit_with_the_same_numbers():
    conn = connect("etl")
    cache.ensure_cache_tables(conn)
    conn.execute("DELETE FROM ops.answer_cache")
    try:
        rt = Runtime.create(get_settings().model_copy(update={"demo_fault_injection": False}))
        rt.health_check = lambda: []
        rt.open_service = lambda: Keep(conn)
        a = Assistant(rt, audit=False, cache=True)
        me = AppUser("live", "Live", "ALL")
        first = a.ask("How many orders were delivered yesterday?", me)
        assert first.status == "answered" and not first.cache_hit and first.usage.tokens_in > 0
        second = a.ask("how many orders were delivered yesterday", me)
        assert second.cache_hit and second.usage.tokens_in == 0
        assert second.answer == first.answer and second.steps[0].rows == first.steps[0].rows
        assert second.latency_ms < first.latency_ms
        print(f"\nfirst {first.latency_ms} ms ({first.usage.tokens_in} tokens in) -> "
              f"cache hit {second.latency_ms} ms (0 tokens)")
    finally:
        conn.rollback()
        conn.close()
