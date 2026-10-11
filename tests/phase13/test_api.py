"""Phase 13, part 2: the HTTP API. Most tests use a stand-in assistant and a fake key table, so
they need no model and no database; the last group uses the real key table and real scoping."""
import pytest
from fastapi.testclient import TestClient

from swiggy_ai.agents.service import Answer
from swiggy_ai.api.app import create_app
from swiggy_ai.security.scope import AppUser
from tests.phase13 import builders as b

KEYS = {"key-for-admin-0000000": AppUser("admin", "Admin", "ALL"),
        "key-for-pune-00000000": AppUser("pune", "Pune manager", "Pune")}


class FakeAssistant:
    def __init__(self, answer=None, boom=None):
        self.calls, self.answer, self.boom = [], answer or b.answered(), boom

    def ask(self, question, user, thread_id=None, fresh=False) -> Answer:
        self.calls.append((question, user, thread_id, fresh))
        if self.boom:
            raise self.boom
        return self.answer


def client(assistant=None, lookup=None, probe=None):
    app = create_app(assistant=assistant or FakeAssistant(),
                     user_lookup=lookup or (lambda key: KEYS.get(key)), health_probe=probe)
    return TestClient(app, raise_server_exceptions=False)


def post(c, body=None, key="key-for-admin-0000000", **kw):
    headers = {"X-API-Key": key} if key else {}
    return c.post("/ask", json=body if body is not None else {"question": "How many orders?"},
                  headers=headers, **kw)


# --- who may ask ------------------------------------------------------------
@pytest.mark.parametrize("key", [None, "", "wrong-key-wrong-key-0", "key-for-admin-000000"])
def test_a_missing_or_wrong_key_is_refused_the_same_way(key):
    fake = FakeAssistant()
    r = post(client(fake), key=key)
    assert r.status_code == 401 and r.json() == {
        "error": "invalid_api_key", "message": "Send a valid key in the X-API-Key header."}
    assert r.headers["www-authenticate"] == "ApiKey"
    assert fake.calls == []  # the assistant was never reached
    assert (key or "zzz") not in r.text or not key  # the key is never echoed back


def test_a_bad_key_is_reported_before_a_bad_body():
    r = post(client(), body={"question": ""}, key="nope")
    assert r.status_code == 401


def test_the_key_chooses_the_user_and_the_scope():
    fake = FakeAssistant()
    c = client(fake)
    post(c, key="key-for-pune-00000000")
    assert fake.calls[0][1].allowed_cities == "Pune" and fake.calls[0][1].user_id == "pune"


# --- what may be asked ------------------------------------------------------------
def test_bad_requests_get_a_short_message_that_does_not_repeat_the_question():
    secret = "my phone is 9876543210 " + "x" * 1100
    for body in ({"question": ""}, {}, {"question": secret}, {"question": "ok", "thread_id": "a b"},
                 {"question": "ok", "thread_id": "x" * 65}, {"question": 5}):
        r = post(client(), body=body)
        assert r.status_code == 422 and r.json()["error"] == "invalid_request"
        assert "9876543210" not in r.text and "input" not in r.json()
    r = post(client(), body={"question": ""})
    assert r.json()["message"] == "Check these fields: question."


def test_a_non_json_body_is_a_422_not_a_crash():
    r = client().post("/ask", content=b"not json", headers={
        "X-API-Key": "key-for-admin-0000000", "Content-Type": "application/json"})
    assert r.status_code == 422


# --- a good answer ------------------------------------------------------------
def test_the_response_has_the_documented_shape():
    r = post(client(FakeAssistant(b.healed())))
    assert r.status_code == 200
    body = r.json()
    for key in ("answer", "sql", "attempts", "checks", "warnings", "usage"):
        assert key in body
    assert body["answer"] == "You received 781 orders." and body["status"] == "answered"
    assert [a["status"] for a in body["attempts"]] == ["error", "ok"]
    assert body["timeline"][0].startswith("Attempt 1 failed: column total_amount")
    assert body["usage"]["tokens_in"] == 3000


def test_refusals_and_clarifications_are_ordinary_answers_with_a_status():
    refused = Answer("refused", "I can only read data.")
    r = post(client(FakeAssistant(refused)))
    assert r.status_code == 200 and r.json()["status"] == "refused"
    r = post(client(FakeAssistant(b.clarify())))
    assert r.json()["clarification"]["options"] == ["revenue"]


def test_a_thread_id_is_generated_returned_and_kept_private_to_the_user():
    fake = FakeAssistant()
    c = client(fake)
    first = post(c).json()["thread_id"]
    assert len(first) == 32 and fake.calls[0][2] == f"admin:{first}"
    again = post(c, body={"question": "and Pune?", "thread_id": "conv-1"}).json()
    assert again["thread_id"] == "conv-1" and fake.calls[1][2] == "admin:conv-1"
    post(c, body={"question": "hi", "thread_id": "conv-1"}, key="key-for-pune-00000000")
    assert fake.calls[2][2] == "pune:conv-1"  # same id, a different user: a different thread


def test_fresh_true_skips_the_cache_and_defaults_to_false():
    fake = FakeAssistant()
    c = client(fake)
    post(c)
    post(c, params={"fresh": "true"})
    assert [call[3] for call in fake.calls] == [False, True]


# --- when things go wrong ------------------------------------------------------------
def test_an_unexpected_error_is_a_500_with_a_reference_and_no_details():
    r = post(client(FakeAssistant(boom=RuntimeError("password=hunter2 sk-proj-abc table x"))))
    assert r.status_code == 500 and r.json()["error"] == "internal_error"
    assert "Reference: " in r.json()["message"]
    for leak in ("hunter2", "sk-proj", "RuntimeError", "Traceback", "table x"):
        assert leak not in r.text


def test_an_unreachable_database_is_a_503_not_a_500():
    import psycopg

    def down(key):
        raise psycopg.OperationalError("connection refused to host db.internal")
    r = post(client(lookup=down))
    assert r.status_code == 503 and r.json()["error"] == "database_unavailable"
    assert "db.internal" not in r.text


def test_an_assistant_that_cannot_start_is_a_503_and_health_still_answers(monkeypatch):
    def cannot(*a, **k):
        raise RuntimeError("OPENAI_API_KEY is empty")
    monkeypatch.setattr("swiggy_ai.api.app.Assistant.for_app", cannot)
    c = TestClient(create_app(user_lookup=lambda k: KEYS.get(k), health_probe=lambda: {
        "database": "ok", "data_quality": "healthy", "schema_alerts": 0}),
        raise_server_exceptions=False)
    r = post(c)
    assert r.status_code == 503 and r.json()["error"] == "assistant_unavailable"
    assert "OPENAI" not in r.text
    assert c.get("/health").status_code == 200


# --- health ------------------------------------------------------------
@pytest.mark.parametrize("info, status, code", [
    ({"database": "ok", "data_quality": "healthy", "schema_alerts": 0}, "ok", 200),
    ({"database": "ok", "data_quality": "warning", "schema_alerts": 0}, "degraded", 200),
    ({"database": "ok", "data_quality": "healthy", "schema_alerts": 2}, "degraded", 200),
    ({"database": "down", "data_quality": "unknown", "schema_alerts": 0}, "down", 503),
])
def test_health_summarises_without_needing_a_key(info, status, code):
    r = client(probe=lambda: dict(info)).get("/health")
    assert r.status_code == code and r.json()["status"] == status


@pytest.mark.db
def test_the_real_health_probe_reports_facts_only():
    r = TestClient(create_app(assistant=FakeAssistant())).get("/health")
    body = r.json()
    assert r.status_code == 200 and body["database"] == "ok"
    assert isinstance(body["data_version"], int) and body["data_quality"] in ("healthy", "warning")
    assert set(body) == {"database", "model_configured", "data_quality", "schema_alerts",
                         "data_version", "last_daily_run", "status"}
    assert "sk-" not in r.text


def test_the_docs_describe_both_endpoints():
    paths = client().get("/openapi.json").json()["paths"]
    assert set(paths) == {"/ask", "/health"}
