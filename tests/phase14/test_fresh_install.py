"""Phase 14, part 3: things only a first run on an empty database reveals.

Running the quick start on a clean copy found that `make silver-full` never finished: tables built
inside one transaction have no planner statistics, so the planner assumed they were empty and chose
nested loops. The fix is to analyse after each load step; these tests keep it fixed.
"""
import pytest

from swiggy_ai.db import connect
from swiggy_ai.pipeline import bronze_loader as bl
from swiggy_ai.pipeline import gold, silver
from swiggy_ai.pipeline.runner import analyze_schema
from swiggy_ai.pipeline.schema_drift import Aliases

pytestmark = pytest.mark.db


@pytest.fixture
def admin():
    c = connect("admin")  # creating a scratch schema needs more than the pipeline role has
    yield c
    c.rollback()
    c.close()


def reltuples(conn, schema, table):
    return conn.execute(
        "SELECT c.reltuples FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace"
        " WHERE n.nspname = %s AND c.relname = %s", (schema, table)).fetchone()[0]


def test_a_table_filled_in_this_transaction_has_no_statistics_until_analysed(admin):
    admin.execute("CREATE SCHEMA zz_stats_test")
    admin.execute("CREATE TABLE zz_stats_test.t (id int PRIMARY KEY, v text)")
    admin.execute("INSERT INTO zz_stats_test.t SELECT g, 'x' FROM generate_series(1, 5000) g")
    assert reltuples(admin, "zz_stats_test", "t") < 0  # "never analysed": the planner is guessing
    analyze_schema(admin, "zz_stats_test")
    assert reltuples(admin, "zz_stats_test", "t") == 5000


def test_analyze_handles_odd_table_names_and_an_empty_schema(admin):
    admin.execute("CREATE SCHEMA zz_stats_test")
    analyze_schema(admin, "zz_stats_test")  # nothing to do, no error
    admin.execute('CREATE TABLE zz_stats_test."Odd Name" (id int)')
    analyze_schema(admin, "zz_stats_test")


def test_silver_and_gold_analyse_after_every_step_that_builds_tables(monkeypatch):
    seen = []
    for module in (silver, gold):
        monkeypatch.setattr(module, "analyze_schema", lambda conn, schema: seen.append(schema))
    with connect("etl") as conn:
        silver.run_silver(conn, analyze=True)
        gold.run_gold(conn, analyze=True)
        conn.rollback()
    steps = [s for s in seen if s == "silver"], [s for s in seen if s == "gold"]
    assert len(steps[0]) >= 7  # one per silver SQL file after the two set-up files
    assert len(steps[1]) >= 4  # one per gold SQL file after the table definitions


def test_a_bronze_load_leaves_the_tables_analysed(tmp_path):
    (tmp_path / "payments").mkdir()
    (tmp_path / "payments" / "payments_2026-01-01.csv").write_text(
        ",".join(bl.SPECS[6].columns) + "\n" + "\n".join(
            f"P{i},O{i},2026-01-01T10:00:00,upi,success,{i}" for i in range(300)) + "\n")
    conn = connect("etl")
    try:
        bl.apply_ddl(conn)
        bl.reset_bronze(conn)
        bl.load_directory(conn, tmp_path, aliases=Aliases(), analyze=True)
        assert reltuples(conn, "bronze", "payments") == 300
    finally:
        conn.rollback()
        conn.close()


# --- the rule: analyse only when the pipeline owns its transaction ----------------
def test_the_default_analyses_on_an_autocommit_connection_but_not_in_a_callers_transaction():
    from swiggy_ai.pipeline.runner import should_analyze
    with connect("etl", autocommit=True) as owned:
        assert should_analyze(owned, None) is True
    with connect("etl") as borrowed:
        borrowed.execute("SELECT 1")  # a transaction is now open: the caller may roll it back
        assert should_analyze(borrowed, None) is False
        assert should_analyze(borrowed, True) is True and should_analyze(borrowed, False) is False
        borrowed.rollback()


def test_a_rolled_back_load_does_not_leave_false_row_counts_behind(tmp_path):
    """ANALYZE's row counts survive a rollback, so the default must not run it in a caller's
    transaction. (This once left test-sized counts behind and made real queries 15x slower.)"""
    with connect("etl", autocommit=True) as c:
        before = reltuples(c, "gold", "fact_order_items")
    conn = connect("etl")
    try:
        gold.run_gold(conn)  # default: a transaction the caller owns
        conn.execute("TRUNCATE gold.fact_refunds, gold.fact_order_items, gold.fact_orders CASCADE")
        gold.run_gold(conn)
        conn.rollback()
    finally:
        conn.close()
    with connect("etl", autocommit=True) as c:
        assert reltuples(c, "gold", "fact_order_items") == before
