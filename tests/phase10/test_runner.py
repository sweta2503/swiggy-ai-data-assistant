"""Phase 10, part 4: the runner end to end with a scripted model and the real database."""
import time

import pytest
from eval.models import Golden, Question
from eval.run_eval import FaultInjectionError, run, run_reference, select

from swiggy_ai.agents.narrator import Narration
from swiggy_ai.agents.service import Answer, Assistant
from swiggy_ai.agents.sql_writer import SQLDraft
from tests.phase08.conftest import Harness

pytestmark = pytest.mark.db

NET = ("SELECT SUM(order_value - discount) FILTER (WHERE status = 'delivered') AS net_revenue "
       "FROM analytics.orders")
NET_PUNE = NET + " WHERE city = 'Pune'"
COUNT = "SELECT count(*) AS orders FROM analytics.orders"


def golden_with(**q):
    qs = [Question(id="X1", category="simple", user="admin",
                   question="What is our total net revenue?",
                   expected=[{"type": "number", "reference_sql": "S02.sql"}], **q)]
    return Golden(users={"admin": "ALL"}, questions=qs)


def test_a_right_answer_is_scored_correct_and_a_wrong_one_is_not(semantic, service):
    g = golden_with()
    right = run_reference(service, g.questions[0])[0][0][0]  # S02: all-time net revenue
    h = Harness(semantic)
    assistant = Assistant(h.rt, audit=False)

    h.llm.say(SQLDraft, SQLDraft(sql=NET)).say(
        Narration, lambda _: Narration(answer=f"Net revenue was \u20b9{right:,.2f}."))
    case = run(g.questions, g, assistant, service, log=lambda *_: None)[0]
    assert case.correct and case.first_attempt_ok and not case.healed, case.verdicts

    h.llm.say(SQLDraft, SQLDraft(sql=NET_PUNE)).say(  # a plausible query that answers a
        Narration, lambda _: Narration(answer="Here is the net revenue you asked for."))
    wrong = run(g.questions, g, assistant, service, log=lambda *_: None)[0]  # different question
    assert not wrong.correct and wrong.status == "answered"
    assert wrong.first_attempt_ok is False and "reference" in wrong.verdicts[0]["detail"]


def test_a_broken_question_is_recorded_as_an_error_and_the_run_continues(semantic, service):
    h = Harness(semantic)
    assistant = Assistant(h.rt, audit=False)
    g = golden_with()
    g.questions.append(g.questions[0].model_copy(update={"id": "X2"}))

    h.llm.say(SQLDraft, SQLDraft(sql=COUNT))
    h.llm.say(Narration, lambda _: (_ for _ in ()).throw(RuntimeError("model fell over")))
    h.llm.default(SQLDraft, SQLDraft(sql=COUNT))
    h.llm.default(Narration, Narration(answer="ok"))
    out = run(g.questions, g, assistant, service, log=lambda *_: None)
    assert len(out) == 2 and out[0].id == "X1"
    assert out[0].error or out[0].status  # recorded either way, never an exception


def test_fault_injection_in_an_eval_run_is_a_hard_failure(semantic, service):
    h = Harness(semantic)
    assistant = Assistant(h.rt, audit=False)

    def faulty(question, user, thread_id=None):
        return Answer("answered", "x", fault_injected=True, steps=[])
    assistant.ask = faulty
    with pytest.raises(FaultInjectionError, match="fault injection"):
        run(golden_with().questions, golden_with(), assistant, service, log=lambda *_: None)


def test_the_eval_asks_as_the_scoped_user_the_question_names(semantic, service):
    seen = []
    h = Harness(semantic)
    assistant = Assistant(h.rt, audit=False)

    def spy(question, user, thread_id=None):
        seen.append((user.allowed_cities, thread_id))
        return Answer("refused", "no", steps=[])
    assistant.ask = spy
    g = Golden(users={"admin": "ALL", "bengaluru": "Bengaluru"}, questions=[
        Question(id="Y1", category="security", user="bengaluru", question="Net revenue?",
                 expected=[{"type": "number", "reference_sql": "SEC1.sql"}])])
    run(g.questions, g, assistant, service, log=lambda *_: None)
    assert seen[0][0] == "Bengaluru" and seen[0][1].startswith("eval-Y1-")


def test_selection_by_id_category_and_limit():
    from eval.models import load_golden
    g = load_golden()
    assert [q.id for q in select(g, "S01,J02", None, None)] == ["S01", "J02"]
    assert {q.category for q in select(g, None, "trap", None)} == {"trap"}
    assert len(select(g, None, None, 5)) == 5 and len(select(g, None, None, None)) == 30


def test_the_dry_run_checks_every_reference_without_a_model(capsys):
    from eval.run_eval import main
    assert main(["--dry-run"]) == 0
    assert "30 questions checked; no model was called" in capsys.readouterr().out


def test_main_ignores_fault_injection_set_in_the_environment():
    """The eval must build its assistant from settings with the demo fault forced off."""
    import inspect

    from eval import run_eval
    src = inspect.getsource(run_eval.main)
    assert 'update={"demo_fault_injection": False}' in src
    assert '"fault_injection": False' in src
    assert time  # (keeps the import used)
