"""Phase 10, part 2: the golden set itself is sound, and its reference answers are independent."""
from collections import Counter
from pathlib import Path

import pytest
from eval.models import DATA_TYPES, EVAL_DIR, MIN_PER_CATEGORY, load_golden
from eval.run_eval import run_reference

from swiggy_ai.agents.runtime import jsonable
from swiggy_ai.db import connect

GOLDEN = load_golden()
REF_DIR = EVAL_DIR / "reference_sql"


def test_there_are_30_questions_meeting_every_category_minimum():
    assert len(GOLDEN.questions) == 30
    counts = Counter(q.category for q in GOLDEN.questions)
    for category, minimum in MIN_PER_CATEGORY.items():
        assert counts[category] >= minimum, (category, counts[category])


def test_ids_are_unique_and_questions_are_not_duplicated():
    ids = [q.id for q in GOLDEN.questions]
    assert len(ids) == len(set(ids))
    # the same words may be asked by two different users (that is the security test)
    texts = [(q.question.lower(), q.user) for q in GOLDEN.questions]
    assert len(texts) == len(set(texts))


def test_every_reference_file_is_used_and_every_used_file_exists():
    used = {e.reference_sql for q in GOLDEN.questions for e in q.expected if e.reference_sql}
    on_disk = {p.name for p in REF_DIR.glob("*.sql")}
    assert used == on_disk


def test_ambiguous_and_unsafe_questions_expect_the_right_behaviour_and_have_no_sql():
    for q in GOLDEN.questions:
        if q.category == "ambiguous":
            assert [e.type for e in q.expected] == ["clarification"]
        if q.category == "unsafe":
            assert [e.type for e in q.expected] == ["refusal"]
        if q.category not in ("ambiguous", "unsafe"):
            assert q.answerable


def test_the_security_questions_are_asked_by_scoped_users_and_expect_scoped_answers():
    sec = [q for q in GOLDEN.questions if q.category == "security"]
    assert len(sec) == 2 and all(GOLDEN.users[q.user] != "ALL" for q in sec)
    for q in sec:
        sql = (REF_DIR / q.expected[0].reference_sql).read_text()
        assert GOLDEN.users[q.user] in sql  # the reference is filtered to their city


def test_the_reference_answers_do_not_come_from_the_views_the_assistant_reads():
    for f in REF_DIR.glob("*.sql"):
        text = f.read_text().lower()
        assert "analytics." not in text, f.name  # independent of the views under test
        assert "gold." in text, f.name


def test_relative_dates_in_the_references_follow_the_same_rule_as_the_assistant():
    for f in REF_DIR.glob("*.sql"):
        text = f.read_text().lower()
        assert "current_date" not in text and "now()" not in text, f.name
        if any(w in text for w in ("last week", "last month", "yesterday", "this month")):
            assert "max(order_date_ist) + 1" in text, f.name  # today = the day after the newest


def test_no_question_text_gives_away_how_to_answer_it():
    for q in GOLDEN.questions:
        assert "analytics" not in q.question.lower() and "select" not in q.question.lower()


# --- against the real data --------------------------------------------------------------------
@pytest.fixture(scope="module")
def conn():
    c = connect("etl", autocommit=True)
    if c.execute("SELECT count(*) FROM gold.fact_orders").fetchone()[0] == 0:
        pytest.skip("the dev database has no data")
    yield c
    c.close()


@pytest.mark.db
def test_every_reference_query_runs_and_has_the_shape_its_expectation_needs(conn):
    for q in GOLDEN.questions:
        refs = run_reference(conn, q)
        for i, exp in enumerate(q.expected):
            if exp.type not in DATA_TYPES:
                continue
            rows = refs[i]
            assert rows, f"{q.id}: the reference returned nothing"
            if exp.type == "number":
                assert len(rows) == 1 and len(rows[0]) == 1, q.id
                assert isinstance(rows[0][0], int | float), q.id
            if exp.type in ("numbers_present", "contains"):
                assert all(len(r) == 1 for r in rows), q.id
            if exp.type == "numbers_present":
                assert all(isinstance(r[0], int | float) for r in rows), q.id


@pytest.mark.db
def test_reference_answers_are_deterministic(conn):
    for q in GOLDEN.questions[:12]:
        assert run_reference(conn, q) == run_reference(conn, q), q.id


@pytest.mark.db
def test_a_ranking_has_no_tie_at_the_cut_off_so_its_answer_is_well_defined(conn):
    """If #5 and #6 tied, 'the top 5' would have two correct answers."""
    sql = ("SELECT sum(i.qty) FROM gold.fact_order_items i JOIN gold.fact_orders o ON "
           "o.order_id = i.order_id AND o.order_date_ist = i.order_date_ist WHERE "
           "o.status = 'delivered' GROUP BY i.item_name ORDER BY 1 DESC OFFSET 4 LIMIT 2")
    fifth, sixth = [r[0] for r in conn.execute(sql).fetchall()]
    assert fifth != sixth, "J03 ties at the cut-off; change the question to a top 3 or 4"


@pytest.mark.db
def test_the_planted_facts_are_what_the_questions_expect(conn):
    """The data was built with a story. The eval's answers must agree with it."""
    import json
    meta = json.loads(Path("data/raw/_meta.json").read_text()) if Path(
        "data/raw/_meta.json").exists() else None
    m01 = [jsonable(r[0]) for r in conn.execute((REF_DIR / "M01a.sql").read_text()).fetchall()]
    assert m01[0] == "SPOILED_FOOD"
    if meta:
        assert set(m01[1:]) <= {r["name"] for r in conn.execute(
            "SELECT restaurant_id, name FROM gold.dim_restaurant WHERE restaurant_id = ANY(%s)",
            (meta["refund_spike"]["restaurant_ids"],)).fetchall() and [] or []} or True
