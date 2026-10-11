"""Phase 13, part 3: the API with the REAL key table and REAL city scoping.

A throw-away user is created with a random key and removed afterwards. The model is scripted.
"""
import secrets

import pytest
from fastapi.testclient import TestClient

from swiggy_ai.agents.narrator import Narration
from swiggy_ai.agents.service import Assistant
from swiggy_ai.agents.sql_writer import SQLDraft
from swiggy_ai.api.app import create_app
from swiggy_ai.db import connect
from swiggy_ai.security.scope import seed_user

pytestmark = pytest.mark.db
COUNT = "SELECT count(*) AS orders FROM analytics.orders"


@pytest.fixture
def users():
    keys = {"pune": secrets.token_urlsafe(24), "all": secrets.token_urlsafe(24)}
    with connect("etl", autocommit=True) as c:
        seed_user(c, "phase13_pune", "Phase 13 Pune", keys["pune"], "Pune")
        seed_user(c, "phase13_all", "Phase 13 All", keys["all"], "ALL")
    yield keys
    with connect("etl", autocommit=True) as c:
        c.execute("DELETE FROM ops.app_users WHERE user_id LIKE 'phase13_%'")


@pytest.fixture
def api(harness):
    h = harness()
    h.llm.default(SQLDraft, SQLDraft(sql=COUNT))
    h.llm.default(Narration, Narration(answer="Here is the order count."))
    return TestClient(create_app(assistant=Assistant(h.rt, audit=False, cache=False)))


def orders(api, key):
    r = api.post("/ask", json={"question": "How many orders are there?"},
                 headers={"X-API-Key": key})
    assert r.status_code == 200, r.text
    return r.json()["data"][0]["rows"][0][0]


def test_a_real_key_works_and_each_key_sees_only_its_own_cities(api, users):
    with connect("etl", autocommit=True) as c:
        total = c.execute("SELECT count(*) FROM gold.fact_orders").fetchone()[0]
        pune = c.execute("SELECT count(*) FROM gold.fact_orders f JOIN gold.dim_city d USING"
                         " (city_sk) WHERE d.city_name = 'Pune'").fetchone()[0]
    assert orders(api, users["all"]) == total
    assert orders(api, users["pune"]) == pune < total


def test_a_switched_off_user_is_refused_immediately(api, users):
    assert orders(api, users["pune"])
    with connect("etl", autocommit=True) as c:
        c.execute("UPDATE ops.app_users SET is_active = false WHERE user_id = 'phase13_pune'")
    r = api.post("/ask", json={"question": "again"}, headers={"X-API-Key": users["pune"]})
    assert r.status_code == 401


def test_the_key_is_stored_only_as_a_hash(users):
    with connect("etl", autocommit=True) as c:
        stored = [r[0] for r in c.execute(
            "SELECT api_key_hash FROM ops.app_users WHERE user_id LIKE 'phase13_%'")]
    assert stored and all(k not in s for s in stored for k in users.values())
    assert all(len(s) == 64 for s in stored)
