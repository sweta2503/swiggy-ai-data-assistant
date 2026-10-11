"""Phase 13, part 4: the command line (stand-in assistant, no model, no database)."""
import json

from swiggy_ai import cli
from swiggy_ai.agents.service import Answer
from swiggy_ai.security.scope import AppUser
from tests.phase13 import builders as b

USERS = {"admin": AppUser("admin", "Admin (all cities)", "ALL"),
         "mumbai_manager": AppUser("mumbai_manager", "Mumbai manager", "Mumbai")}


class Fake:
    def __init__(self, *answers):
        self.answers, self.calls = list(answers), []

    def ask(self, question, user, thread_id=None, fresh=False):
        self.calls.append((question, user.user_id, thread_id, fresh))
        return self.answers.pop(0) if len(self.answers) > 1 else self.answers[0]


def run(argv, fake=None, read=None):
    lines: list[str] = []
    code = cli.main(argv, assistant=fake or Fake(b.answered()), find_user=USERS.get,
                    all_users=lambda: list(USERS.values()), out=lines.append,
                    read=read or (lambda prompt: "exit"))
    return code, "\n".join(lines)


def test_an_answer_shows_the_text_the_sql_what_happened_and_the_cost():
    code, text = run(["ask", "How many orders?", "--user", "admin"], Fake(b.healed()))
    assert code == 0
    assert text.startswith("You received 781 orders.")
    assert "SQL used:\n  SELECT 1" in text
    assert "1. Attempt 1 failed: column total_amount does not exist" in text
    assert "2. The healer rewrote the query" in text and "3. Attempt 2 succeeded (83 ms)" in text
    assert "[Repaired automatically (1 extra attempt)]" in text and "[Scope: ALL]" in text
    assert "3 model call(s), 3,000 tokens in, 200 out" in text and "thread " in text


def test_the_user_and_the_flags_reach_the_assistant():
    fake = Fake(b.answered())
    run(["ask", "q", "--user", "mumbai_manager", "--fresh"], fake)
    question, user, thread, fresh = fake.calls[0]
    assert (question, user, fresh) == ("q", "mumbai_manager", True) and thread


def test_json_output_is_the_same_payload_as_the_api():
    code, text = run(["ask", "q", "--user", "admin", "--json"])
    body = json.loads(text)
    assert code == 0 and body["answer"] == "You received 781 orders."
    assert {"answer", "sql", "attempts", "checks", "warnings", "usage"} <= set(body)


def test_clarifications_show_the_options_and_refusals_are_not_errors():
    code, text = run(["ask", "Best?", "--user", "admin"], Fake(b.clarify()))
    assert code == 0 and "Best by what?" in text and "options: revenue" in text
    assert run(["ask", "x", "--user", "admin"], Fake(Answer("refused", "No.")))[0] == 0
    assert run(["ask", "x", "--user", "admin"], Fake(Answer("failed", "Model down.")))[0] == 1


def test_warnings_are_printed():
    _, text = run(["ask", "q", "--user", "admin"], Fake(b.answered(
        warnings=["data quality warning: gold check failed"])))
    assert "WARNING: data quality warning: gold check failed" in text


def test_an_unknown_user_gets_the_list_of_real_ones():
    code, text = run(["ask", "q", "--user", "nobody"])
    assert code == 2 and "unknown user 'nobody'" in text and "admin, mumbai_manager" in text


def test_users_lists_ids_cities_and_names():
    code, text = run(["users"])
    assert code == 0 and "mumbai_manager" in text and "Mumbai" in text and "Admin (all" in text


def test_chat_keeps_one_thread_for_a_whole_conversation_and_stops_on_exit():
    fake = Fake(b.answered())
    typed = iter(["first question", "", "and the second", "exit"])
    code, text = run(["chat", "--user", "admin"], fake, read=lambda prompt: next(typed))
    assert code == 0 and "Asking as Admin (all cities) (ALL)" in text
    assert [c[0] for c in fake.calls] == ["first question", "and the second"]
    assert fake.calls[0][2] == fake.calls[1][2]


def test_a_start_up_failure_is_a_sentence_not_a_traceback(monkeypatch):
    def cannot(*a, **k):
        raise RuntimeError("OPENAI_API_KEY is not set")
    monkeypatch.setattr(cli.Assistant, "for_app", cannot)
    lines: list[str] = []
    code = cli.main(["ask", "q", "--user", "admin"], find_user=USERS.get,
                    all_users=lambda: [], out=lines.append)
    assert code == 3 and "could not start" in lines[0] and "OPENAI_API_KEY" in lines[0]
    assert "Traceback" not in lines[0]
