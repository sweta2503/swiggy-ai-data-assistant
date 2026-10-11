"""Phase 12, part 1: cache keys, and the cache table itself (rolled back)."""
import pytest

from swiggy_ai.db import connect
from swiggy_ai.ops import cache


# --- keys (no database) -------------------------------------------------------------------------
def test_questions_that_differ_only_in_case_spacing_or_punctuation_share_a_key():
    base = cache.normalize("What is our net revenue?")
    for variant in ("what is our net revenue", "  WHAT is   our\tnet revenue ?? ",
                    "What is our net revenue.", "What is our net revenue!\n"):
        assert cache.normalize(variant) == base == "what is our net revenue"


def test_questions_that_mean_different_things_do_not_share_a_key():
    a = cache.normalize("net revenue in Pune last month")
    assert a != cache.normalize("net revenue in Mumbai last month")
    assert a != cache.normalize("net revenue in Pune this month")
    assert cache.normalize("") == "" and cache.normalize(None) == ""


def test_the_scope_hash_ignores_order_case_and_spaces_but_not_membership():
    assert cache.scope_hash("ALL") == cache.scope_hash("all") == "ALL"
    assert cache.scope_hash("Pune, Mumbai") == cache.scope_hash("mumbai,PUNE")
    assert cache.scope_hash("Pune") != cache.scope_hash("Pune,Mumbai")
    assert cache.scope_hash("Pune") != "ALL"
    assert "pune" not in cache.scope_hash("Pune")  # a hash, not the city list


def test_the_key_changes_with_question_version_and_scope():
    k = cache.cache_key("q", 1, "ALL")
    assert k == cache.cache_key("q", 1, "ALL")
    assert len({k, cache.cache_key("q2", 1, "ALL"), cache.cache_key("q", 2, "ALL"),
                cache.cache_key("q", 1, "abc")}) == 4
    assert cache.cache_key("a", 12, "b") != cache.cache_key("a1", 2, "b")  # no ambiguous joins


# --- the table (a real database, one transaction, rolled back) ----------------------
@pytest.fixture
def conn():
    c = connect("etl")
    cache.ensure_cache_tables(c)
    yield c
    c.rollback()
    c.close()


def test_a_stored_answer_comes_back_and_counts_its_hits(conn):
    key = cache.cache_key("q", cache.get_data_version(conn), "ALL")
    assert cache.lookup(conn, key) is None
    cache.store(conn, key, "q", cache.get_data_version(conn), "ALL", {"answer": "42"})
    data, created = cache.lookup(conn, key)
    assert data == {"answer": "42"} and created is not None
    cache.lookup(conn, key)
    assert conn.execute("SELECT hits FROM ops.answer_cache WHERE cache_key = %s",
                        (key,)).fetchone()[0] == 2


def test_storing_again_replaces_the_entry_and_resets_its_hits(conn):
    v = cache.get_data_version(conn)
    key = cache.cache_key("q", v, "ALL")
    cache.store(conn, key, "q", v, "ALL", {"answer": "old"})
    cache.lookup(conn, key)
    cache.store(conn, key, "q", v, "ALL", {"answer": "new"})
    assert cache.lookup(conn, key)[0] == {"answer": "new"}
    assert conn.execute("SELECT count(*), max(hits) FROM ops.answer_cache WHERE cache_key = %s",
                        (key,)).fetchone() == (1, 1)


def test_bumping_the_data_version_retires_every_older_answer(conn):
    v = cache.get_data_version(conn)
    old = cache.cache_key("q", v, "ALL")
    cache.store(conn, old, "q", v, "ALL", {"answer": "old data"})
    new_version = cache.bump_data_version(conn)
    assert new_version == v + 1 == cache.get_data_version(conn)
    assert cache.lookup(conn, old) is None
    assert cache.cache_key("q", new_version, "ALL") != old  # and the new key differs anyway


def test_clear_and_stats(conn):
    v = cache.get_data_version(conn)
    cache.clear(conn)
    cache.store(conn, "k1", "a", v, "ALL", {})
    cache.store(conn, "k2", "b", v, "ALL", {})
    cache.lookup(conn, "k1")
    assert cache.stats(conn) == {"entries": 2, "hits": 1, "data_version": v}
    assert cache.clear(conn) == 2 and cache.stats(conn)["entries"] == 0


def test_there_is_exactly_one_version_row(conn):
    assert conn.execute("SELECT count(*) FROM ops.data_version").fetchone()[0] == 1
    with pytest.raises(Exception, match="check|violates"):
        conn.execute("INSERT INTO ops.data_version (id, version) VALUES (2, 1)")
