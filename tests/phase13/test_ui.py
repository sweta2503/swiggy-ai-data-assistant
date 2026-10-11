"""Phase 13, part 5: the chat page, driven headlessly with Streamlit's own test runner.
The assistant is a stand-in, so there is no model and no database."""
from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

from swiggy_ai.security.scope import AppUser
from swiggy_ai.ui import logic
from tests.phase13 import builders as b

PAGE = str(Path(__file__).resolve().parents[2] / "src" / "swiggy_ai" / "ui" / "app.py")
USERS = [AppUser("admin", "Admin (all cities)", "ALL"),
         AppUser("mumbai_manager", "Mumbai manager", "Mumbai")]


@pytest.fixture
def page(monkeypatch):
    asked = []
    monkeypatch.setattr(logic, "demo_users", lambda: USERS)
    monkeypatch.setattr(logic, "pipeline_status", lambda: logic.Status(
        data_version=3, last_run="published at 2026-10-11 06:00", alerts=0, quality="healthy"))

    def fake_ask(question, user, thread, fresh):
        asked.append((question, user.user_id, thread, fresh))
        return b.healed() if "broken" in question else b.answered()
    monkeypatch.setattr(logic, "ask", fake_ask)
    at = AppTest.from_file(PAGE, default_timeout=20)
    at.run()
    at.asked = asked
    return at


def texts(at):
    return " ".join(m.value for m in at.markdown)


def test_the_page_loads_with_a_user_selector_and_pipeline_status(page):
    assert not page.exception
    assert [o for o in page.selectbox[0].options] == ["Admin (all cities) (ALL)",
                                                      "Mumbai manager (Mumbai)"]
    assert "Data version 3" in " ".join(c.value for c in page.sidebar.caption)
    assert "Last daily run: published at 2026-10-11 06:00" in " ".join(
        c.value for c in page.sidebar.caption)


def test_a_question_shows_the_answer_the_checks_and_the_attempts_timeline(page):
    page.chat_input[0].set_value("How many orders?").run()
    assert not page.exception
    assert page.asked[0][:2] == ("How many orders?", "admin") and page.asked[0][3] is False
    assert "You received 781 orders." in texts(page)
    assert "All 4 result checks passed" in texts(page) and "Scope: ALL" in texts(page)
    labels = [e.label for e in page.expander]
    assert "What happened (1 attempt)" in labels and "SQL used" in labels
    assert any(label.startswith("Data:") for label in labels)


def test_a_repaired_answer_opens_the_timeline_and_tells_the_story(page):
    page.chat_input[0].set_value("a broken one").run()
    story = next(e for e in page.expander if e.label.startswith("What happened"))
    assert story.label == "What happened (2 attempts)"
    body = " ".join(m.value for m in story.markdown)
    assert "Attempt 1 failed: column total_amount does not exist" in body
    assert "The healer rewrote the query" in body and "Attempt 2 succeeded" in body
    assert "Repaired automatically" in texts(page)


def test_each_user_has_their_own_conversation_and_thread(page):
    page.chat_input[0].set_value("admin question").run()
    page.selectbox[0].select("Mumbai manager (Mumbai)").run()
    assert "admin question" not in texts(page)  # not shown to the other user
    page.chat_input[0].set_value("mumbai question").run()
    assert page.asked[1][1] == "mumbai_manager" and page.asked[0][2] != page.asked[1][2]
    page.selectbox[0].select("Admin (all cities) (ALL)").run()
    assert "admin question" in texts(page) and "mumbai question" not in texts(page)


def test_the_skip_cache_box_is_passed_through(page):
    page.checkbox[0].check().run()
    page.chat_input[0].set_value("again").run()
    assert page.asked[0][3] is True


def test_new_conversation_starts_a_new_thread(page):
    page.chat_input[0].set_value("one").run()
    page.button[0].click().run()
    page.chat_input[0].set_value("two").run()
    assert page.asked[0][2] != page.asked[1][2]
    assert "one" not in texts(page).replace("two", "")


def test_without_users_the_page_explains_what_to_do(monkeypatch):
    monkeypatch.setattr(logic, "demo_users", lambda: [])
    monkeypatch.setattr(logic, "pipeline_status", lambda: logic.Status())
    at = AppTest.from_file(PAGE, default_timeout=20).run()
    assert "make seed-users" in at.error[0].value
