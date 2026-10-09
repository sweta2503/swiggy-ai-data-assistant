"""Phase 3: silver fixes every defect injected in phase 1, and incremental == full rebuild.

Each test runs inside a transaction that is rolled back, so real bronze/silver data is untouched.
"""
import json
import shutil
from datetime import date
from pathlib import Path

import pytest

from swiggy_ai.datagen.config import GenConfig
from swiggy_ai.datagen.generate import generate
from swiggy_ai.db import connect
from swiggy_ai.pipeline import bronze_loader as bl
from swiggy_ai.pipeline import silver as sv

pytestmark = pytest.mark.db


@pytest.fixture(scope="module")
def raw(tmp_path_factory) -> Path:
    out = tmp_path_factory.mktemp("raw")
    generate(GenConfig(seed=42, out_dir=out, end_date=date(2026, 9, 30), days=60, n_orders=4000,
                       n_customers=700, n_restaurants=90, n_partners=120))
    return out


@pytest.fixture(scope="module")
def defects(raw) -> dict:
    return json.loads((raw / "_defects.json").read_text())


@pytest.fixture(scope="module")
def meta(raw) -> dict:
    return json.loads((raw / "_meta.json").read_text())


@pytest.fixture
def conn():
    c = connect("etl")
    bl.apply_ddl(c)
    bl.reset_bronze(c)  # TRUNCATE is transactional, so the real data returns on rollback
    sv.run_silver(c, full=True)  # creates silver tables and empties them
    yield c
    c.rollback()
    c.close()


@pytest.fixture
def silver(conn, raw):
    bl.load_directory(conn, raw)
    return sv.run_silver(conn)


def q(conn, sql, *args):
    return conn.execute(sql, args).fetchall()


def one(conn, sql, *args):
    return conn.execute(sql, args).fetchone()[0]


def rejected_keys(conn, table, reason) -> set[str]:
    return {k for (k,) in q(conn, "SELECT row_key FROM silver.rejected_rows"
                            " WHERE table_name = %s AND reason = %s", table, reason)}


# --- dedup + counts -------------------------------------------------------------------------
def test_orders_are_deduplicated(conn, silver, meta, defects):
    bronze_rows = one(conn, "SELECT count(*) FROM bronze.orders")
    assert bronze_rows == meta["unique_orders"] + defects["duplicate_orders"]["extra_rows"]
    n_silver = one(conn, "SELECT count(*) FROM silver.orders")
    assert n_silver == one(conn, "SELECT count(DISTINCT order_id) FROM silver.orders")
    outliers = defects["delivery_minutes_outliers"]["rows"]
    assert n_silver == meta["unique_orders"] - outliers  # each order once, minus rejects


def test_status_spellings_collapse_to_four_values(conn, silver):
    assert {s for (s,) in q(conn, "SELECT DISTINCT status FROM silver.orders")} <= {
        "delivered", "cancelled", "refunded", "failed"}
    assert one(conn, "SELECT count(*) FROM silver.rejected_rows"
                     " WHERE reason = 'unknown_status'") == 0
    assert one(conn, "SELECT count(*) FROM silver.orders s JOIN bronze.orders b USING (order_id)"
                     " WHERE b.status = 'DLVRD' AND s.status <> 'delivered'") == 0
    assert one(conn, "SELECT count(*) FROM silver.orders s JOIN bronze.orders b USING (order_id)"
                     " WHERE b.status = 'DLVRD'") > 0


# --- time zones -----------------------------------------------------------------------------
def test_order_times_converted_utc_to_ist(conn, silver):
    bad = one(conn, "SELECT count(*) FROM silver.orders WHERE order_ts_ist"
                    " - (order_ts_utc AT TIME ZONE 'UTC') <> interval '5 hours 30 minutes'")
    assert bad == 0
    assert one(conn, "SELECT count(*) FROM silver.orders"
                     " WHERE order_date_ist <> order_ts_ist::date") == 0
    # evening-UTC orders belong to the NEXT calendar day in India
    crossed = one(conn, "SELECT count(*) FROM silver.orders"
                        " WHERE order_date_ist <> (order_ts_utc AT TIME ZONE 'UTC')::date")
    assert crossed > 0


def test_naive_ist_payments_line_up_with_orders(conn, silver):
    gaps = q(conn, "SELECT extract(epoch FROM p.payment_ts_utc - o.order_ts_utc)"
                   " FROM silver.payments p JOIN silver.orders o USING (order_id)")
    assert len(gaps) > 1000
    assert all(0 <= g <= 91 for (g,) in gaps)  # off by 19,800s if IST were treated as UTC


# --- nulls and flags -------------------------------------------------------------------------
def test_missing_city_and_cuisine_get_default_and_flag(conn, silver, defects):
    flagged = {c for (c,) in q(conn, "SELECT DISTINCT customer_id FROM silver.customer_snapshots"
                                     " WHERE city_was_null AND city = 'Unknown'")}
    assert flagged == set(defects["null_city_customers"]["customer_ids"]) and flagged
    cuisines = {r for (r,) in q(conn, "SELECT restaurant_id FROM silver.restaurants"
                                      " WHERE cuisine_was_blank AND cuisine = 'Unknown'")}
    assert cuisines == set(defects["blank_cuisine_restaurants"]["restaurant_ids"])
    assert one(conn, "SELECT count(*) FROM silver.customer_snapshots WHERE city IS NULL") == 0


# --- rejects ---------------------------------------------------------------------------------
def test_outlier_orders_are_quarantined_with_their_raw_row(conn, silver, defects):
    rejected = rejected_keys(conn, "orders", "delivery_minutes_out_of_range")
    assert rejected == set(defects["delivery_minutes_outliers"]["order_ids"]) and rejected
    assert one(conn, "SELECT count(*) FROM silver.orders WHERE order_id = ANY(%s)",
               list(rejected)) == 0
    bad = q(conn, "SELECT row_json->>'delivery_minutes' FROM silver.rejected_rows"
                  " WHERE table_name = 'orders' AND reason = 'delivery_minutes_out_of_range'")
    assert any(float(m) < 0 for (m,) in bad)  # the original value is preserved


def test_orphan_items_rejected_and_children_of_rejected_orders_follow(conn, silver, defects):
    orphan_rows = defects["orphan_order_items"]["rows"]
    assert len(rejected_keys(conn, "order_items", "orphan")) == orphan_rows
    orphan_orders = {o for (o,) in q(
        conn, "SELECT row_json->>'order_id' FROM silver.rejected_rows"
              " WHERE table_name = 'order_items' AND reason = 'orphan'")}
    assert orphan_orders == set(defects["orphan_order_items"]["order_ids"]) and orphan_orders
    parents = {o for (o,) in q(
        conn, "SELECT DISTINCT row_json->>'order_id' FROM silver.rejected_rows"
              " WHERE table_name = 'order_items' AND reason = 'parent_order_rejected'")}
    assert parents <= set(defects["delivery_minutes_outliers"]["order_ids"]) and parents


def test_no_item_or_refund_without_a_valid_order(conn, silver):
    assert one(conn, "SELECT count(*) FROM silver.order_items i LEFT JOIN silver.orders o"
                     " USING (order_id) WHERE o.order_id IS NULL") == 0
    assert one(conn, "SELECT count(*) FROM silver.refunds r LEFT JOIN silver.orders o"
                     " USING (order_id) WHERE o.order_id IS NULL") == 0


def test_every_bronze_row_is_accounted_for(conn, silver):
    bronze_items = one(conn, "SELECT count(DISTINCT order_item_id) FROM bronze.order_items")
    rejected = one(conn, "SELECT count(*) FROM silver.rejected_rows"
                         " WHERE table_name = 'order_items'")
    assert one(conn, "SELECT count(*) FROM silver.order_items") + rejected == bronze_items


def test_lineage_columns_survive(conn, silver):
    assert one(conn, "SELECT count(*) FROM silver.orders WHERE _source_file IS NULL"
                     " OR _batch_id IS NULL") == 0


# --- incremental behaviour -----------------------------------------------------------------------
def fingerprint(conn):
    tables = list(sv.TABLES.values())
    counts = {t: one(conn, f"SELECT count(*) FROM silver.{t}") for t in tables}
    rejects = q(conn, "SELECT table_name, reason, count(*) FROM silver.rejected_rows GROUP BY 1, 2"
                      " ORDER BY 1, 2")
    totals = one(conn, "SELECT sum(order_value) FROM silver.orders")
    return counts, rejects, totals


def test_second_run_processes_nothing(conn, silver):
    before = fingerprint(conn)
    again = sv.run_silver(conn)
    assert again.batches_processed == 0
    assert fingerprint(conn) == before


def test_incremental_equals_full_rebuild(conn, raw, tmp_path):
    bl.load_directory(conn, raw)
    sv.run_silver(conn, full=True)
    expected = fingerprint(conn)

    # Start over with the last 4 days of transactional files held back (like a daily feed)
    partial = tmp_path / "data"
    shutil.copytree(raw, partial)
    held_back = []
    for folder in ("orders", "order_items", "payments", "refunds", "ratings"):
        for f in sorted((partial / folder).glob("*"))[-4:]:
            shutil.move(f, tmp_path / f"{folder}__{f.name}")
            held_back.append((folder, f.name))
    bl.reset_bronze(conn)
    sv.run_silver(conn, full=True)
    bl.load_directory(conn, partial)
    first = sv.run_silver(conn)
    assert first.batches_processed > 0
    assert fingerprint(conn) != expected  # the held-back days are really missing

    for folder, name in held_back:  # the next days arrive
        shutil.move(tmp_path / f"{folder}__{name}", partial / folder / name)
    bl.load_directory(conn, partial)
    second = sv.run_silver(conn)
    assert 0 < second.batches_processed < first.batches_processed  # only the new files
    assert fingerprint(conn) == expected


def test_late_parent_promotes_orphan_refund(conn, raw, tmp_path):
    """A refund can arrive before the (late) order it belongs to; it must not be lost."""
    bl.load_directory(conn, raw)
    sv.run_silver(conn, full=True)
    full = one(conn, "SELECT count(*) FROM silver.refunds")

    # hold back the order files, keep the refunds
    partial = tmp_path / "data"
    shutil.copytree(raw, partial)
    shutil.rmtree(partial / "orders")
    bl.reset_bronze(conn)
    sv.run_silver(conn, full=True)
    bl.load_directory(conn, partial)
    sv.run_silver(conn)
    assert one(conn, "SELECT count(*) FROM silver.refunds") == 0
    assert one(conn, "SELECT count(*) FROM silver.rejected_rows WHERE reason = 'orphan'"
                     " AND table_name = 'refunds'") == one(
        conn, "SELECT count(DISTINCT refund_id) FROM bronze.refunds")  # all held, none lost

    shutil.copytree(raw / "orders", partial / "orders")
    bl.load_directory(conn, partial)
    sv.run_silver(conn)
    assert one(conn, "SELECT count(*) FROM silver.refunds") == full
