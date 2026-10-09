"""Phase 2: bronze loads are idempotent, traceable, and never drop a row silently.

Every test works inside a transaction that is rolled back at the end, so the real bronze
tables (loaded by `make bronze`) are never touched.
"""
import csv
import json
import shutil
from datetime import date
from pathlib import Path

import pytest

from swiggy_ai.datagen.config import GenConfig
from swiggy_ai.datagen.generate import generate
from swiggy_ai.db import connect
from swiggy_ai.pipeline import bronze_loader as bl

pytestmark = pytest.mark.db


@pytest.fixture(scope="module")
def raw(tmp_path_factory) -> Path:
    out = tmp_path_factory.mktemp("raw")
    generate(GenConfig(seed=42, out_dir=out, end_date=date(2026, 9, 30), days=60, n_orders=3000,
                       n_customers=600, n_restaurants=80, n_partners=100))
    return out


@pytest.fixture
def conn():
    c = connect("etl")  # autocommit off: everything below is one big transaction
    bl.apply_ddl(c)
    bl.reset_bronze(c)  # TRUNCATE is transactional: tests see empty tables, real data comes back
    yield c
    c.rollback()
    c.close()


def count(conn, sql, *args) -> int:
    return conn.execute(sql, args).fetchone()[0]


def csv_rows(pattern: str, root: Path) -> int:
    total = 0
    for f in root.glob(pattern):
        with f.open(newline="") as fh:
            total += sum(1 for _ in csv.reader(fh)) - 1
    return total


# --- happy path ----------------------------------------------------------------------------
def test_row_counts_match_the_files(conn, raw):
    r = bl.load_directory(conn, raw)
    meta = json.loads((raw / "_meta.json").read_text())
    assert r.rows_rejected == 0 and r.files_rejected == 0
    assert r.rows_loaded["orders"] == meta["order_rows_in_files"]  # duplicates are kept in bronze
    assert r.rows_loaded["order_items"] == meta["item_rows_in_files"]
    assert r.rows_loaded["payments"] == csv_rows("payments/*.csv", raw)
    assert r.rows_loaded["customer_snapshots"] == csv_rows("customers/*.csv", raw)
    assert r.rows_loaded["restaurants"] == csv_rows("restaurants.csv", raw)
    for table, n in r.rows_loaded.items():
        assert count(conn, f"SELECT count(*) FROM bronze.{table}") >= n


def test_second_run_loads_nothing(conn, raw):
    first = bl.load_directory(conn, raw)
    second = bl.load_directory(conn, raw)
    assert first.files_loaded > 0
    assert second.files_loaded == 0 and second.total_rows == 0
    assert second.files_skipped == second.files_seen == first.files_seen


def test_ledger_has_one_correct_row_per_file(conn, raw):
    r = bl.load_directory(conn, raw)
    rows = conn.execute("SELECT file_hash, source_file, row_count, status FROM bronze.load_ledger"
                        " WHERE source_file = ANY(%s)",
                        ([p.relative_to(raw).as_posix() for p in raw.rglob("*")
                          if p.suffix in (".csv", ".jsonl")],)).fetchall()
    assert len(rows) == r.files_loaded
    for file_hash, source_file, _n, status in rows:
        assert status == "loaded"
        assert file_hash == bl.file_sha256(raw / source_file)
    # ledger row_count == rows physically in the table for that file hash
    h, src, n, _ = next(x for x in rows if x[1].startswith("orders/"))
    assert count(conn, "SELECT count(*) FROM bronze.orders WHERE _file_hash = %s", h) == n


def test_bronze_keeps_data_as_received(conn, raw):
    bl.load_directory(conn, raw)
    statuses = {s for (s,) in conn.execute("SELECT DISTINCT status FROM bronze.orders")}
    assert {"DLVRD", "DELIVERED", "CANCELED"} <= statuses  # not cleaned yet
    assert count(conn, "SELECT count(*) FROM bronze.orders WHERE delivery_partner_id IS NULL") > 0
    assert count(conn, "SELECT count(*) FROM bronze.restaurants WHERE cuisine = ''") == 5
    col = conn.execute("SELECT data_type FROM information_schema.columns WHERE"
                       " table_schema='bronze' AND table_name='orders'"
                       " AND column_name='order_value'").fetchone()
    assert col[0] == "text"


def test_metadata_columns_are_filled(conn, raw):
    bl.load_directory(conn, raw)
    nulls = count(conn, "SELECT count(*) FROM bronze.orders WHERE _source_file IS NULL OR"
                  " _file_hash IS NULL OR _ingested_at IS NULL OR _batch_id IS NULL")
    assert nulls == 0
    assert count(conn, "SELECT min(_row_number) FROM bronze.orders") == 1
    # one batch per file
    assert count(conn, "SELECT count(DISTINCT _batch_id) FROM bronze.orders") == \
        count(conn, "SELECT count(DISTINCT _file_hash) FROM bronze.orders")


def test_python_registry_matches_database_columns(conn):
    for spec in bl.SPECS:
        cols = [c for (c,) in conn.execute(
            "SELECT column_name FROM information_schema.columns WHERE table_schema='bronze'"
            " AND table_name=%s ORDER BY ordinal_position", (spec.table,))]
        assert cols == [*spec.columns, *bl.META_COLUMNS], spec.table


# --- changed files ----------------------------------------------------------------------------
def test_changed_file_with_same_name_loads_as_new_batch(conn, raw, tmp_path):
    work = tmp_path / "data"
    shutil.copytree(raw, work)
    bl.load_directory(conn, work)

    target = next((work / "payments").glob("*.csv"))
    with target.open("a") as f:
        f.write("PAYX,ORDX,2026-09-01 10:00:00,upi,success,99.0\n")
    r = bl.load_directory(conn, work)

    rel = target.relative_to(work).as_posix()
    assert r.files_changed == 1 and r.files_loaded == 1
    assert count(conn, "SELECT count(*) FROM bronze.load_ledger WHERE source_file = %s", rel) == 2
    assert count(conn, "SELECT count(DISTINCT _batch_id) FROM bronze.payments"
                 " WHERE _source_file = %s", rel) == 2


# --- bad data is quarantined, never dropped ----------------------------------------------------
def test_bad_rows_go_to_reject_table(conn, tmp_path):
    (tmp_path / "orders").mkdir()
    good = {c: "x" for c in bl.SPECS[4].columns}
    (tmp_path / "orders" / "orders_2026-01-01.jsonl").write_text(
        json.dumps(good) + "\n{not json\n\n[1, 2, 3]\n" + json.dumps(good) + "\n")
    (tmp_path / "payments").mkdir()
    (tmp_path / "payments" / "payments_2026-01-01.csv").write_text(
        "payment_id,order_id,payment_ts,method,status,amount\nP1,O1,t,upi,success,1\nP2,O2\n")

    r = bl.load_directory(conn, tmp_path)
    assert r.rows_loaded == {"orders": 2, "payments": 1}
    assert r.rows_rejected == 3
    reasons = dict(conn.execute("SELECT row_number, reason FROM bronze.rejected_files_rows"
                                " WHERE source_file = 'orders/orders_2026-01-01.jsonl'").fetchall())
    assert set(reasons) == {2, 4} and "invalid JSON" in reasons[2] and "not an object" in reasons[4]
    short = conn.execute("SELECT raw_content, reason FROM bronze.rejected_files_rows"
                         " WHERE source_file = 'payments/payments_2026-01-01.csv'").fetchone()
    assert "P2" in short[0] and "expected 6 fields" in short[1]
    ledger = conn.execute("SELECT rejected_count FROM bronze.load_ledger WHERE source_file"
                          " = 'orders/orders_2026-01-01.jsonl'").fetchone()
    assert ledger[0] == 2


def test_wrong_header_rejects_whole_file_and_is_not_retried(conn, tmp_path):
    (tmp_path / "payments").mkdir()
    (tmp_path / "payments" / "payments_2026-01-01.csv").write_text(
        "payment_id,order_id,payment_ts,method,status,total\nP1,O1,t,upi,success,1\n")
    first = bl.load_directory(conn, tmp_path)
    assert first.files_rejected == 1 and first.total_rows == 0
    assert count(conn, "SELECT count(*) FROM bronze.payments"
                 " WHERE _source_file = 'payments/payments_2026-01-01.csv'") == 0
    assert conn.execute("SELECT status FROM bronze.load_ledger WHERE source_file ="
                        " 'payments/payments_2026-01-01.csv'").fetchone()[0] == "rejected_header"
    assert count(conn, "SELECT count(*) FROM bronze.rejected_files_rows WHERE row_number IS NULL"
                 " AND source_file = 'payments/payments_2026-01-01.csv'") == 1
    again = bl.load_directory(conn, tmp_path)
    assert again.files_skipped == 1 and again.files_rejected == 0


def test_multiline_csv_field_is_one_record(conn, tmp_path):
    (tmp_path / "customers").mkdir()
    header = ",".join(bl.SPECS[3].columns)
    (tmp_path / "customers" / "customers_snapshot_2026-01-01.csv").write_text(
        header + '\n2026-01-01,C1,A,9,a@b.c,"12 Main St\nFlat 4",560001,Pune,new,2026-01-01\n')
    bl.load_directory(conn, tmp_path)
    row = conn.execute("SELECT address_line, _row_number FROM bronze.customer_snapshots"
                       " WHERE customer_id = 'C1'").fetchone()
    assert row == ("12 Main St\nFlat 4", 1)


# --- atomicity ----------------------------------------------------------------------------------
def test_failed_file_leaves_no_ledger_row(conn, tmp_path):
    f = tmp_path / "payments"
    f.mkdir()
    path = f / "payments_2026-01-01.csv"
    path.write_text("payment_id,order_id,payment_ts,method,status,amount\nP1,O1,t,upi,ok,1\n")
    broken = bl.FileSpec("table_that_does_not_exist", "x", "csv", bl.SPECS[6].columns)
    digest = bl.file_sha256(path)
    with pytest.raises(Exception, match="table_that_does_not_exist"):
        bl.load_file(conn, broken, path, "payments/payments_2026-01-01.csv", digest)
    conn.rollback()  # a failed statement poisons the outer test transaction; reset it
    bl.apply_ddl(conn)
    assert count(conn, "SELECT count(*) FROM bronze.load_ledger WHERE file_hash = %s", digest) == 0
