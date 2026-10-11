"""Phase 11, part 4: the whole loop when a view column is renamed under the assistant.

A column of the REAL analytics.orders view is renamed for the length of each test and put back in
a `finally`, so a crashed test cannot leave the database renamed (and the fixture also repairs a
leftover rename from an earlier crash). The catalog changes only in a scratch copy.
"""
import shutil

import pytest

from swiggy_ai.agents.healer import Repair
from swiggy_ai.agents.narrator import Narration
from swiggy_ai.agents.sql_writer import SQLDraft
from swiggy_ai.db import connect
from swiggy_ai.semantic.loader import SEMANTIC_DIR, load_semantic
from swiggy_ai.semantic.refresh import refresh_catalog

pytestmark = pytest.mark.db
OLD_STYLE = "SELECT platform, count(*) AS orders FROM analytics.orders GROUP BY platform"
NEW_STYLE = "SELECT app_platform, count(*) AS orders FROM analytics.orders GROUP BY app_platform"


def view_columns(conn):
    return {r[0] for r in conn.execute(
        "SELECT column_name FROM information_schema.columns WHERE table_schema = 'analytics'"
        " AND table_name = 'orders'")}


@pytest.fixture
def renamed_view():
    """analytics.orders.platform is called app_platform while the test runs."""
    conn = connect("etl", autocommit=True)
    if "app_platform" in view_columns(conn):  # left over from a crashed run
        conn.execute("ALTER VIEW analytics.orders RENAME COLUMN app_platform TO platform")
    conn.execute("ALTER VIEW analytics.orders RENAME COLUMN platform TO app_platform")
    try:
        yield conn
    finally:
        conn.execute("ALTER VIEW analytics.orders RENAME COLUMN app_platform TO platform")
        conn.close()
    with connect("etl", autocommit=True) as c:
        assert "platform" in view_columns(c)


@pytest.fixture
def scratch(tmp_path):
    d = tmp_path / "semantic"
    shutil.copytree(SEMANTIC_DIR, d)
    return d


def run(harness, semantic, repair):
    h = harness()
    h.rt.semantic = semantic
    h.llm.say(SQLDraft, SQLDraft(sql=OLD_STYLE))  # the writer still remembers the old name
    h.llm.say(Repair, repair)
    h.llm.default(Narration, Narration(answer="Here is the split by app."))
    return h, h.ask("How many orders per platform?")


def test_before_the_refresh_the_healer_cannot_know_the_new_name(harness, renamed_view):
    seen = []

    def stuck(user_input):
        seen.append(user_input)
        return Repair(sql=OLD_STYLE, changed="nothing to go on")  # all it can do is repeat itself

    h, s = run(harness, load_semantic(SEMANTIC_DIR), stuck)
    assert s.status != "answered"
    assert "Error class: undefined_column" in seen[0]
    assert 'column "platform" does not exist' in seen[0]
    assert "app_platform" not in seen[0]  # the stale catalog still says `platform`


def test_after_refresh_catalog_the_healer_repairs_the_query_using_the_new_name(
        harness, renamed_view, scratch):
    plan = refresh_catalog(renamed_view, scratch)
    assert plan.renamed == [("analytics.orders", "platform", "app_platform")]
    fresh = load_semantic(scratch)
    assert fresh.catalog.table("analytics.orders").column("app_platform").allowed_values  # kept

    seen = []

    def uses_the_catalog(user_input):
        seen.append(user_input)
        assert "app_platform text" in user_input  # the refreshed catalog is in front of it
        return Repair(sql=NEW_STYLE, changed="the column is now called app_platform")

    h, s = run(harness, fresh, uses_the_catalog)
    assert s.status == "answered"
    assert [(a.source, a.status, a.error_class) for a in s.attempts] == [
        ("writer", "error", "undefined_column"), ("healer", "ok", "")]
    assert {r[0] for r in s.results[0].rows} == {"android", "ios", "web"}
    assert s.results[0].healed == 1
    assert any("repaired automatically" in w for w in s.warnings)
    shown = seen[0].replace("app_platform text", "")
    assert "platform text" not in shown  # no stale name left in what the healer was shown
