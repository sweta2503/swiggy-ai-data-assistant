"""Phase 6, part 1: the semantic files are valid, strict, and say what the business agreed.

No database needed.
"""
import shutil
from pathlib import Path

import pytest
import yaml

from swiggy_ai.semantic.context_builder import build_context, estimate_tokens
from swiggy_ai.semantic.loader import (
    SEMANTIC_DIR,
    SemanticError,
    expression_columns,
    load_glossary,
    load_semantic,
)
from swiggy_ai.semantic.models import REQUIRED_METRICS


@pytest.fixture(scope="module")
def sem():
    return load_semantic()


# --- the real files ---------------------------------------------------------------------------
def test_real_files_load_and_agree(sem):
    assert len(sem.catalog.tables) == 8
    assert len(sem.metrics.metrics) >= 10
    assert all(sem.metrics.metric(n) for n in REQUIRED_METRICS)


def test_every_column_is_documented_and_none_is_personal_data(sem):
    for t in sem.catalog.tables:
        for c in t.columns:
            assert len(c.description) >= 10, f"{t.name}.{c.name}"
            assert not c.pii
    masked = [(t.name, c.name) for t in sem.catalog.tables for c in t.columns if c.masked]
    assert masked == [("analytics.customers", "masked_phone")]


def test_every_table_warns_about_its_traps(sem):
    facts = {t.name: t for t in sem.catalog.tables if t.kind == "fact"}
    orders_notes = " ".join(facts["analytics.orders"].do_not)
    assert "order_items" in orders_notes and "net_revenue" in orders_notes  # fan-out and revenue
    assert "order_value" in " ".join(facts["analytics.order_items"].do_not)
    for t in facts.values():
        assert t.do_not and t.grain


# --- the definitions the business agreed ------------------------------------------------------
def test_net_revenue_is_items_minus_discount_for_delivered_only(sem):
    m = sem.metrics.metric("net_revenue")
    assert expression_columns(m.sql_expression) == {"order_value", "discount", "status"}
    assert "delivery_fee" not in m.sql_expression and "taxes" not in m.sql_expression
    assert "'delivered'" in m.sql_expression


def test_rates_use_the_agreed_numerators_and_denominators(sem):
    cancel = sem.metrics.metric("cancellation_rate").sql_expression
    assert "'cancelled'" in cancel and "NULLIF(COUNT(*), 0)" in cancel  # over ALL orders placed
    refund = sem.metrics.metric("refund_rate").sql_expression
    assert "'refunded'" in refund and "'delivered'" in refund.split("NULLIF")[1]
    aov = sem.metrics.metric("aov").sql_expression
    assert aov.count("'delivered'") == 2  # revenue and count are both delivered-only


def test_conventions_say_ist_monday_and_a_data_driven_today(sem):
    c = sem.metrics.conventions
    assert c.timezone == "Asia/Kolkata" and c.week_starts_on == "Monday"
    assert "data_as_of" in c.reference_date and "CURRENT_DATE" in c.reference_date
    assert {"last_week", "last_month", "yesterday"} <= set(c.period_templates)
    assert "date_trunc('week'" in c.period_templates["last_week"]


def test_glossary_is_parsed_into_terms(sem):
    by_term = {g.term: g for g in sem.glossary}
    assert "revenue" in by_term["Net revenue"].also_called
    assert "GMV" in by_term["Gross order value"].also_called
    assert len(by_term["Fan-out"].definition) > 40


# --- bad input is rejected with a clear message -----------------------------------------------
@pytest.fixture
def workdir(tmp_path):
    d = tmp_path / "semantic"
    shutil.copytree(SEMANTIC_DIR, d)
    return d


def edit(path: Path, fn) -> None:
    data = yaml.safe_load(path.read_text())
    fn(data)
    path.write_text(yaml.safe_dump(data, sort_keys=False))


def problems(d: Path) -> str:
    with pytest.raises(SemanticError) as e:
        load_semantic(d)
    return str(e.value)


def test_metric_on_an_unknown_view_is_rejected(workdir):
    edit(workdir / "metrics.yml", lambda d: d["metrics"][0].update(source="analytics.nope"))
    assert "source 'analytics.nope' is not in the catalog" in problems(workdir)


def test_metric_using_a_missing_column_is_rejected(workdir):
    edit(workdir / "metrics.yml",
         lambda d: d["metrics"][0].update(sql_expression="SUM(order_valu)"))
    assert "uses column 'order_valu'" in problems(workdir)


def test_unparsable_expression_is_rejected(workdir):
    edit(workdir / "metrics.yml",
         lambda d: d["metrics"][0].update(sql_expression="COUNT((*"))
    assert "does not parse" in problems(workdir)


def test_statement_instead_of_expression_is_rejected(workdir):
    edit(workdir / "metrics.yml",
         lambda d: d["metrics"][0].update(sql_expression="COUNT(*); DROP TABLE x"))
    assert "one expression" in problems(workdir)


def test_time_column_must_be_a_date(workdir):
    edit(workdir / "metrics.yml", lambda d: d["metrics"][0].update(time_column="city"))
    assert "time_column 'city' is not a date column" in problems(workdir)


def test_missing_required_metric_is_rejected(workdir):
    edit(workdir / "metrics.yml",
         lambda d: d.update(metrics=[m for m in d["metrics"] if m["name"] != "aov"]))
    assert "required metric 'aov' is missing" in problems(workdir)


def test_duplicate_metric_is_rejected(workdir):
    edit(workdir / "metrics.yml", lambda d: d["metrics"].append(dict(d["metrics"][0])))
    assert "duplicate metrics" in problems(workdir)


def test_a_typo_in_a_field_name_is_an_error_not_silently_ignored(workdir):
    edit(workdir / "metrics.yml", lambda d: d["metrics"][0].update(descripton="oops"))
    assert "descripton" in problems(workdir)


def test_pii_may_not_be_exposed(workdir):
    edit(workdir / "catalog.yml",
         lambda d: d["tables"][3]["columns"][0].update(pii=True))
    assert "personal data (pii: true) may not be exposed" in problems(workdir)


def test_duplicate_column_in_a_table_is_rejected(workdir):
    edit(workdir / "catalog.yml",
         lambda d: d["tables"][0]["columns"].append(dict(d["tables"][0]["columns"][0])))
    assert "duplicate columns" in problems(workdir)


def test_invalid_yaml_is_reported_not_crashed_on(workdir):
    (workdir / "catalog.yml").write_text("tables: [unclosed")
    assert "not valid YAML" in problems(workdir)


def test_duplicate_glossary_term_is_rejected(workdir):
    with (workdir / "glossary.md").open("a") as f:
        f.write("\n## AOV\nA second, conflicting definition of AOV.\n")
    assert "term 'aov' appears twice" in problems(workdir)
    assert len(load_glossary(workdir / "glossary.md")) > 20


# --- the context builder ----------------------------------------------------------------------
def ctx(question, **kw):
    return build_context(question, **kw)


def test_revenue_question_gets_the_agreed_definition_and_only_relevant_views():
    b = ctx("Top 5 restaurants by net revenue in Bengaluru last month")
    assert b.metrics == ["net_revenue"]
    assert "SUM(order_value - discount) FILTER (WHERE status = 'delivered')" in b.text
    assert b.tables[0] == "analytics.orders"
    assert "reason_code" not in b.text  # the refunds view is not needed
    assert "Asia/Kolkata" in b.text and "Monday" in b.text and "data_as_of" in b.text
    assert "last_month:" in b.text
    assert "Net revenue" in b.glossary


def test_refund_question_gets_the_refunds_view_and_its_reason_codes():
    b = ctx("Why did refunds increase in Mumbai last month?")
    assert "analytics.refunds" in b.tables and "refund_rate" in b.metrics
    assert "SPOILED_FOOD" in b.text and "group by reason_code" in b.text.lower()


@pytest.mark.parametrize("question, metric", [
    ("What was our sales yesterday?", "net_revenue"),
    ("How much GMV last week?", "gross_order_value"),
    ("what is the average basket size", "aov"),
    ("How many signups did we get in March?", "new_customers"),
    ("average delivery time by city", "avg_delivery_minutes"),
    ("cancel rate for Pune", "cancellation_rate"),
    ("How many orders did we get last week?", "orders"),
])
def test_everyday_words_map_to_the_right_metric(question, metric):
    assert metric in ctx(question).metrics


def test_relative_dates_pull_in_the_reference_view():
    assert "analytics.data_as_of" in ctx("orders last week").tables
    assert "analytics.data_as_of" not in ctx("orders in Pune").tables


def test_same_question_gives_the_same_context():
    q = "average order value by cuisine in Pune last month"
    assert ctx(q).text == ctx(q).text


def test_a_vague_question_still_gets_sensible_context():
    vague = ctx("Best customers?")
    assert vague.metrics == [] and vague.tables == ["analytics.customers"]
    odd = ctx("what is the weather like")
    assert odd.tables == ["analytics.orders"]  # falls back to the main fact table


def test_glossary_terms_are_included_when_asked_about():
    assert "AOV" in ctx("what does AOV mean?").glossary


def test_context_fits_the_default_budget_for_typical_questions():
    for q in ["Top 5 restaurants by net revenue in Bengaluru last month",
              "Month-over-month net revenue growth by cuisine",
              "Why did refunds increase in Mumbai last month?",
              "Revenue by customer segment", "Which city has the highest average delivery time?"]:
        b = ctx(q)
        assert b.tokens <= 2200, q
        assert b.tokens == estimate_tokens(b.text)


def test_a_small_budget_trims_in_order_and_keeps_the_essentials():
    q = "Why did refunds increase in Mumbai last month?"
    full, small = ctx(q), ctx(q, budget_tokens=900)
    assert small.trimmed and small.tokens <= 900 < full.tokens
    assert small.notes  # says what was cut
    for essential in ("Asia/Kolkata", "refund_rate", "analytics.refunds", "Do not:"):
        assert essential in small.text
    assert "Other views available" not in small.text and "Terms" not in small.text


def test_even_an_impossible_budget_keeps_the_conventions_and_the_best_view():
    b = ctx("net revenue last month", budget_tokens=50)
    assert b.trimmed and "Asia/Kolkata" in b.text and "analytics.orders" in b.text
    assert "Do not:" in b.text
