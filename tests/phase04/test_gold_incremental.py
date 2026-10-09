"""Phase 4: re-runs and incremental loads. Each test has its own connection (see helpers)."""
import shutil
from pathlib import Path

import pytest

from swiggy_ai.pipeline import bronze_loader as bl
from swiggy_ai.pipeline import gold as gd
from swiggy_ai.pipeline import silver as sv
from tests.phase04.helpers import build, fresh_connection, make_raw, one, q

pytestmark = pytest.mark.db


@pytest.fixture(scope="module")
def raw(tmp_path_factory) -> Path:
    return make_raw(tmp_path_factory.mktemp("raw"))


@pytest.fixture
def own(raw):
    c = fresh_connection()
    yield c
    c.rollback()
    c.close()


def snapshot_of_gold(conn) -> dict:
    return {
        "counts": {t: one(conn, f"SELECT count(*) FROM gold.{t}") for t in gd.TABLES},
        "revenue": one(conn, "SELECT sum(net_revenue) FROM gold.fact_orders"),
        "items": one(conn, "SELECT sum(line_total) FROM gold.fact_order_items"),
        "versions": q(conn, "SELECT customer_id, valid_from, valid_to, city, segment, is_current"
                            " FROM gold.dim_customer ORDER BY customer_id, valid_from"),
    }


def test_second_run_changes_nothing(own, raw):
    build(own, raw)
    before = snapshot_of_gold(own)
    again = gd.run_gold(own, data_dir=raw)
    assert all(n == 0 for n in again.added.values())
    assert snapshot_of_gold(own) == before


def test_changed_silver_row_updates_gold(own, raw):
    build(own, raw)
    oid = one(own, "SELECT order_id FROM gold.fact_orders WHERE status = 'delivered' LIMIT 1")
    own.execute("UPDATE silver.orders SET status = 'cancelled', _ingested_at = clock_timestamp()"
                " WHERE order_id = %s", (oid,))
    gd.run_gold(own, data_dir=raw)
    assert q(own, "SELECT status, net_revenue FROM gold.fact_orders WHERE order_id = %s",
             oid) == [("cancelled", 0)]


def test_incremental_equals_full_rebuild_and_keeps_surrogate_keys(own, raw, tmp_path):
    build(own, raw)
    expected = snapshot_of_gold(own)

    partial = tmp_path / "data"
    shutil.copytree(raw, partial)
    held = []
    for folder, n in (("orders", 4), ("order_items", 4), ("payments", 4), ("refunds", 4),
                      ("ratings", 4), ("customers", 15)):
        for f in sorted((partial / folder).glob("*"))[-n:]:
            shutil.move(f, tmp_path / f"{folder}__{f.name}")
            held.append((folder, f.name))
    bl.reset_bronze(own)
    sv.run_silver(own, full=True)
    gd.run_gold(own, full=True, data_dir=partial)
    build(own, partial)
    assert snapshot_of_gold(own) != expected  # the held-back days are really missing
    keys_before = dict(((c, v), sk) for c, v, sk in q(
        own, "SELECT customer_id, valid_from, customer_sk FROM gold.dim_customer"))
    open_before = one(own, "SELECT count(*) FROM gold.dim_customer WHERE is_current")

    for folder, name in held:  # the next days arrive
        shutil.move(tmp_path / f"{folder}__{name}", partial / folder / name)
    build(own, partial)

    assert snapshot_of_gold(own) == expected
    keys_after = dict(((c, v), sk) for c, v, sk in q(
        own, "SELECT customer_id, valid_from, customer_sk FROM gold.dim_customer"))
    assert all(keys_after[k] == sk for k, sk in keys_before.items())  # keys never change
    closed = one(own, "SELECT count(*) FROM gold.dim_customer WHERE NOT is_current"
                      " AND customer_sk = ANY(%s)", list(keys_before.values()))
    assert closed > 0 and open_before > 0  # at least one version was closed by a later change
