"""Phase 11, part 2: the loader with a real database (every test is rolled back)."""
import json
from pathlib import Path

import pytest

from swiggy_ai.db import connect
from swiggy_ai.pipeline import bronze_loader as bl
from swiggy_ai.pipeline import schema_drift as drift
from swiggy_ai.pipeline.schema_drift import Aliases

pytestmark = pytest.mark.db

ORDER = {"order_id": "O1", "customer_id": "C1", "restaurant_id": "R1",
         "delivery_partner_id": "P1", "city": "Pune", "status": "Delivered", "platform": "ios",
         "order_ts": "2026-01-01T10:00:00Z", "delivered_ts": "2026-01-01T10:30:00Z",
         "delivery_minutes": 30, "order_value": 500.0,
         "discount": 50.0, "delivery_fee": 25.0, "taxes": 20.0}
RENAME = Aliases(renames={"orders": {"order_amount": "order_value"}},
                 ignore_new_columns={"orders": ["tip_amount"]})


@pytest.fixture
def conn():
    c = connect("etl")
    bl.apply_ddl(c)
    bl.reset_bronze(c)
    yield c
    c.rollback()
    c.close()


def write_orders(root: Path, name: str, records: list[dict]) -> Path:
    (root / "orders").mkdir(exist_ok=True)
    path = root / "orders" / name
    path.write_text("\n".join(json.dumps(r) for r in records) + "\n")
    return path


def drifted(order_id="O1"):
    rec = {("order_amount" if k == "order_value" else k): v for k, v in ORDER.items()}
    rec["order_id"], rec["tip_amount"] = order_id, 7
    return rec


def n(conn, sql, *a):
    return conn.execute(sql, a).fetchone()[0]


def test_the_expected_schemas_are_recorded_once_and_never_overwritten(conn, tmp_path):
    bl.load_directory(conn, tmp_path)
    cols = drift.expected_columns(conn, "orders")
    assert cols == list(bl.SPECS[4].columns)
    assert n(conn, "SELECT count(*) FROM ops.expected_schemas") == len(bl.SPECS)
    fp = n(conn, "SELECT fingerprint FROM ops.expected_schemas WHERE file_type = 'orders'")
    assert fp == drift.fingerprint(cols)
    conn.execute("UPDATE ops.expected_schemas SET columns = '[\"x\"]' WHERE file_type='orders'")
    bl.load_directory(conn, tmp_path)  # the loader must not 'fix' the owner's expectation
    assert drift.expected_columns(conn, "orders") == ["x"]


def test_a_reordered_header_loads_by_name_not_by_position(conn, tmp_path):
    (tmp_path / "payments").mkdir()
    cols = list(bl.SPECS[6].columns)
    swapped = [cols[5], *cols[1:5], cols[0]]  # amount first, payment_id last
    (tmp_path / "payments" / "payments_2026-01-01.csv").write_text(
        ",".join(swapped) + "\n99.5,O1,2026-01-01T10:00:00,upi,success,P1\n")
    r = bl.load_directory(conn, tmp_path)
    assert r.files_loaded == 1 and r.files_quarantined == 0
    row = conn.execute("SELECT payment_id, amount, method FROM bronze.payments").fetchone()
    assert row == ("P1", "99.5", "upi")


def test_a_changed_file_is_parked_with_an_alert_and_nothing_reaches_bronze(conn, tmp_path):
    write_orders(tmp_path, "orders_2026-01-02.jsonl", [drifted("O1"), drifted("O2")])
    r = bl.load_directory(conn, tmp_path, aliases=Aliases())
    assert (r.files_quarantined, r.files_loaded, r.total_rows) == (1, 0, 0)
    assert n(conn, "SELECT count(*) FROM bronze.orders") == 0
    assert n(conn, "SELECT count(*) FROM bronze.load_ledger") == 0
    assert n(conn, "SELECT max(_batch_id) FROM bronze.orders") is None  # silver sees no new batch
    q = conn.execute("SELECT file_type, row_count, status, observed_columns FROM"
                     " bronze.quarantine_files").fetchone()
    assert q[:3] == ("orders", 2, "open") and "tip_amount" in q[3]
    rec = n(conn, "SELECT record FROM bronze.quarantine_rows ORDER BY row_number LIMIT 1")
    assert rec["order_amount"] == "500.0"  # kept as received (text), nothing lost
    a = conn.execute("SELECT status, missing_columns, new_columns, suggested_renames FROM"
                     " ops.schema_alerts").fetchone()
    assert a == ("open", ["order_value"], ["order_amount", "tip_amount"],
                 {"order_amount": "order_value"})


def test_the_same_file_seen_again_raises_no_second_alert_and_stores_no_second_copy(conn, tmp_path):
    write_orders(tmp_path, "orders_2026-01-02.jsonl", [drifted()])
    for _ in range(3):
        bl.load_directory(conn, tmp_path, aliases=Aliases())
    assert n(conn, "SELECT count(*) FROM ops.schema_alerts") == 1
    assert n(conn, "SELECT count(*) FROM bronze.quarantine_files") == 1
    assert n(conn, "SELECT count(*) FROM bronze.quarantine_rows") == 1


def test_good_files_still_load_while_a_changed_one_waits(conn, tmp_path):
    write_orders(tmp_path, "orders_2026-01-01.jsonl", [ORDER])
    write_orders(tmp_path, "orders_2026-01-02.jsonl", [drifted("O2")])
    r = bl.load_directory(conn, tmp_path, aliases=Aliases())
    assert r.files_loaded == 1 and r.files_quarantined == 1
    assert [x[0] for x in conn.execute("SELECT order_id FROM bronze.orders").fetchall()] == ["O1"]


def test_approving_the_rename_alone_keeps_the_file_parked(conn, tmp_path):
    write_orders(tmp_path, "orders_2026-01-02.jsonl", [drifted()])
    bl.load_directory(conn, tmp_path, aliases=Aliases())
    r = bl.load_directory(conn, tmp_path, aliases=Aliases(
        renames={"orders": {"order_amount": "order_value"}}))
    assert r.files_quarantined == 1 and r.files_loaded == 0
    assert n(conn, "SELECT count(*) FROM bronze.orders") == 0


def test_after_approval_the_parked_file_loads_mapped_and_the_alert_closes(conn, tmp_path):
    write_orders(tmp_path, "orders_2026-01-02.jsonl", [drifted("O9")])
    bl.load_directory(conn, tmp_path, aliases=Aliases())
    r = bl.load_directory(conn, tmp_path, aliases=RENAME)
    assert (r.files_loaded, r.files_released, r.files_quarantined) == (1, 1, 0)
    row = conn.execute("SELECT order_id, order_value, discount FROM bronze.orders").fetchone()
    assert row == ("O9", "500.0", "50.0")  # order_amount landed in order_value
    assert n(conn, "SELECT status FROM bronze.quarantine_files") == "released"
    status, how = conn.execute("SELECT status, resolution FROM ops.schema_alerts").fetchone()
    assert status == "resolved" and "order_amount" in how and "tip_amount" in how
    assert n(conn, "SELECT count(*) FROM bronze.load_ledger WHERE status = 'loaded'") == 1
    again = bl.load_directory(conn, tmp_path, aliases=RENAME)  # and it is not loaded twice
    assert again.files_loaded == 0 and again.files_skipped == 1
    assert n(conn, "SELECT count(*) FROM bronze.orders") == 1


def test_a_file_that_arrives_already_approved_loads_with_no_alert(conn, tmp_path):
    write_orders(tmp_path, "orders_2026-01-02.jsonl", [drifted("O9")])
    r = bl.load_directory(conn, tmp_path, aliases=RENAME)
    assert r.files_loaded == 1 and r.files_released == 0
    assert n(conn, "SELECT count(*) FROM ops.schema_alerts") == 0


def test_a_dropped_column_is_quarantined_in_a_csv_file_type_too(conn, tmp_path):
    (tmp_path / "payments").mkdir()
    (tmp_path / "payments" / "payments_2026-01-01.csv").write_text(
        "payment_id,order_id,payment_ts,method,status\nP1,O1,t,upi,success\n")
    r = bl.load_directory(conn, tmp_path, aliases=Aliases())
    assert r.files_quarantined == 1
    assert n(conn, "SELECT missing_columns FROM ops.schema_alerts") == ["amount"]
    assert n(conn, "SELECT count(*) FROM bronze.payments") == 0


def test_a_malformed_row_in_a_good_file_is_still_a_row_level_reject_not_drift(conn, tmp_path):
    (tmp_path / "orders").mkdir()
    (tmp_path / "orders" / "orders_2026-01-01.jsonl").write_text(
        json.dumps(ORDER) + "\nnot json at all\n")
    r = bl.load_directory(conn, tmp_path)
    assert r.files_loaded == 1 and r.rows_rejected == 1 and r.files_quarantined == 0


def test_open_alerts_become_a_warning_beside_answers(conn, tmp_path):
    assert drift.alert_warning(conn) == []
    write_orders(tmp_path, "orders_2026-01-02.jsonl", [drifted()])
    bl.load_directory(conn, tmp_path, aliases=Aliases())
    [msg] = drift.alert_warning(conn)
    assert "1 source file" in msg and "orders_2026-01-02.jsonl" in msg and "quarantine" in msg
    bl.load_directory(conn, tmp_path, aliases=RENAME)
    assert drift.alert_warning(conn) == []


def test_the_runtime_shows_that_warning_next_to_answers(conn, tmp_path):
    from swiggy_ai.agents.runtime import Runtime

    class Keep:  # hands out the test's connection and does not close it
        def __enter__(self):
            return conn

        def __exit__(self, *a):
            return False

    write_orders(tmp_path, "orders_2026-01-02.jsonl", [drifted()])
    bl.load_directory(conn, tmp_path, aliases=Aliases())
    rt = Runtime(settings=None, semantic=None, llm=None, open_assistant=None,
                 open_service=lambda: Keep(), clock=None)
    assert any("quarantine" in w for w in rt.health_warnings())
