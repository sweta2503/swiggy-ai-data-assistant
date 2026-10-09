"""Phase 5, part 3: the corruption demo (needs the admin role, so it has its own module)."""
import pytest

from swiggy_ai.db import connect
from swiggy_ai.dq.runner import (
    DEMO_SCHEMA,
    assert_gold_healthy,
    default_as_of,
    run_checks,
    run_corruption_demo,
)
from swiggy_ai.pipeline import bronze_loader as bl
from swiggy_ai.pipeline import gold as gd
from swiggy_ai.pipeline import silver as sv
from tests.phase04.helpers import make_raw, one

pytestmark = pytest.mark.db


@pytest.fixture
def admin(tmp_path_factory):
    raw = make_raw(tmp_path_factory.mktemp("raw"))
    c = connect("admin")  # one connection builds the data AND runs the demo, so it can see it
    bl.apply_ddl(c)
    bl.reset_bronze(c)
    sv.run_silver(c, full=True)
    gd.run_gold(c, full=True)
    bl.load_directory(c, raw)
    sv.run_silver(c)
    gd.run_gold(c, data_dir=raw)
    yield c, raw
    c.rollback()
    c.close()


def test_duplicating_a_day_is_caught_and_real_gold_is_untouched(admin):
    conn, raw = admin
    before = one(conn, "SELECT count(*) FROM gold.fact_orders")
    run_checks(conn, as_of=default_as_of(raw))  # a good standard run exists
    assert assert_gold_healthy(conn)

    clean, corrupted, day = run_corruption_demo(conn, default_as_of(raw))

    assert clean.failures == []  # the copy starts healthy
    names = {r.check.name for r in corrupted.blocking_failures}
    assert f"unique:{DEMO_SCHEMA}.fact_orders.order_id" in names
    assert "reconciliation:order_count_by_day" in names
    assert "reconciliation:net_revenue_by_day" in names
    assert any(r.check.name.startswith("row_count_delta") for r in corrupted.failures)

    assert one(conn, "SELECT count(*) FROM gold.fact_orders") == before  # real gold unchanged
    assert one(conn, "SELECT count(*) FROM information_schema.schemata WHERE schema_name = %s",
               DEMO_SCHEMA) == 0  # the scratch copy is gone
    assert one(conn, "SELECT count(*) FROM ops.dq_results WHERE run_kind = 'demo'") > 0
    assert assert_gold_healthy(conn)  # the demo did not make the real data look unhealthy
    assert day
