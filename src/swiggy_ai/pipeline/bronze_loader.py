"""Bronze loader: land source files in the database exactly as received.

Rules that make it safe to run any number of times:
  1. A file is identified by the sha256 of its CONTENT, not its name.
  2. Hash already in `bronze.load_ledger`  ->  skip (idempotent).
  3. Each file loads in ONE transaction together with its ledger row, so a crash
     leaves either everything or nothing for that file.
  4. Unparseable rows go to `bronze.rejected_files_rows`; they are never silently dropped.
"""
import argparse
import csv
import hashlib
import json
import logging
import sys
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

import psycopg

from swiggy_ai.db import connect
from swiggy_ai.logging_setup import setup_logging
from swiggy_ai.pipeline.runner import run_sql_dir

log = logging.getLogger("bronze")
META_COLUMNS = ("_source_file", "_file_hash", "_ingested_at", "_batch_id", "_row_number")
BRONZE_TABLES = [
    "orders", "payments", "customer_snapshots", "order_items", "refunds", "ratings",
    "restaurants", "delivery_partners", "menu_items",
]


@dataclass(frozen=True)
class FileSpec:
    table: str
    glob: str
    fmt: str  # "csv" or "jsonl"
    columns: tuple[str, ...]


# Load order matters a little: reference data first, so batch ids rise in dependency order.
SPECS = [
    FileSpec("restaurants", "restaurants.csv", "csv",
             ("restaurant_id", "name", "cuisine", "city", "rating", "onboarded_date", "is_active")),
    FileSpec("delivery_partners", "delivery_partners.csv", "csv",
             ("partner_id", "name", "phone", "city", "joined_date")),
    FileSpec("menu_items", "menu_items.csv", "csv",
             ("item_id", "restaurant_id", "item_name", "category", "price")),
    FileSpec("customer_snapshots", "customers/customers_snapshot_*.csv", "csv",
             ("snapshot_date", "customer_id", "name", "phone", "email", "address_line", "pincode",
              "city", "segment", "signup_date")),
    FileSpec("orders", "orders/orders_*.jsonl", "jsonl",
             ("order_id", "customer_id", "restaurant_id", "delivery_partner_id", "city", "status",
              "platform", "order_ts", "delivered_ts", "delivery_minutes", "order_value",
              "discount", "delivery_fee", "taxes")),
    FileSpec("order_items", "order_items/order_items_*.csv", "csv",
             ("order_item_id", "order_id", "item_id", "item_name", "qty", "unit_price")),
    FileSpec("payments", "payments/payments_*.csv", "csv",
             ("payment_id", "order_id", "payment_ts", "method", "status", "amount")),
    FileSpec("refunds", "refunds/refunds_*.csv", "csv",
             ("refund_id", "order_id", "refund_ts", "refund_amount", "reason_code")),
    FileSpec("ratings", "ratings/ratings_*.csv", "csv",
             ("order_id", "restaurant_rating", "delivery_rating", "rated_ts")),
]


@dataclass
class LoadReport:
    files_seen: int = 0
    files_loaded: int = 0
    files_skipped: int = 0
    files_changed: int = 0  # same name as a loaded file, different content
    files_rejected: int = 0
    rows_loaded: dict[str, int] = field(default_factory=dict)
    rows_rejected: int = 0

    @property
    def total_rows(self) -> int:
        return sum(self.rows_loaded.values())


def file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


# ---------------------------------------------------------------- parsing ----------------
def parse_csv(path: Path, spec: FileSpec):
    """Returns (rows, rejects, header_ok): rows=[(row_no, values)], rejects=[(row_no, raw, why)]."""
    with path.open(newline="") as f:
        reader = csv.reader(f)
        header = next(reader, [])
        if tuple(header) != spec.columns:
            return [], [], False
        rows, rejects = [], []
        for n, rec in enumerate(reader, start=1):  # n counts records, not lines (fields can wrap)
            if len(rec) != len(spec.columns):
                rejects.append((n, json.dumps(rec), f"expected {len(spec.columns)} fields, "
                                f"got {len(rec)}"))
            else:
                rows.append((n, rec))  # blanks stay '' because bronze is "as received"
    return rows, rejects, True


def parse_jsonl(path: Path, spec: FileSpec):
    rows, rejects = [], []
    for n, line in enumerate(path.read_text().splitlines(), start=1):
        if not line.strip():
            continue
        try:
            rec = json.loads(line)
        except json.JSONDecodeError as e:
            rejects.append((n, line, f"invalid JSON: {e.msg}"))
            continue
        if not isinstance(rec, dict):
            rejects.append((n, line, "JSON line is not an object"))
            continue
        # JSON null -> SQL NULL; everything else becomes text
        rows.append((n, [None if rec.get(c) is None else str(rec[c]) for c in spec.columns]))
    return rows, rejects, True


# ---------------------------------------------------------------- loading ----------------
def load_file(conn: psycopg.Connection, spec: FileSpec, path: Path, rel_name: str,
              file_hash: str) -> tuple[str, int, int]:
    """Load one file inside a transaction. Returns (status, rows_loaded, rows_rejected)."""
    parse = parse_csv if spec.fmt == "csv" else parse_jsonl
    rows, rejects, header_ok = parse(path, spec)
    ingested_at = datetime.now(UTC)

    with conn.transaction():
        batch_id = conn.execute("SELECT nextval('bronze.batch_seq')").fetchone()[0]
        if not header_ok:
            conn.execute(
                "INSERT INTO bronze.rejected_files_rows"
                "(source_file, file_hash, batch_id, row_number, raw_content, reason)"
                " VALUES (%s, %s, %s, NULL, NULL, %s)",
                (rel_name, file_hash, batch_id, f"header does not match expected {spec.columns}"))
            status, loaded = "rejected_header", 0
        else:
            cols = ", ".join((*spec.columns, *META_COLUMNS))
            copy_sql = f"COPY bronze.{spec.table} ({cols}) FROM STDIN"
            with conn.cursor() as cur, cur.copy(copy_sql) as cp:
                for n, values in rows:
                    cp.write_row((*values, rel_name, file_hash, ingested_at, batch_id, n))
            if rejects:
                with conn.cursor() as cur:
                    cur.executemany(
                        "INSERT INTO bronze.rejected_files_rows"
                        "(source_file, file_hash, batch_id, row_number, raw_content, reason)"
                        " VALUES (%s, %s, %s, %s, %s, %s)",
                        [(rel_name, file_hash, batch_id, n, raw, why) for n, raw, why in rejects])
            status, loaded = "loaded", len(rows)
        conn.execute(
            "INSERT INTO bronze.load_ledger"
            "(file_hash, source_file, row_count, rejected_count, batch_id, status)"
            " VALUES (%s, %s, %s, %s, %s, %s)",
            (file_hash, rel_name, loaded, len(rejects), batch_id, status))
    return status, loaded, len(rejects)


def load_directory(conn: psycopg.Connection, data_dir: Path) -> LoadReport:
    """Load every file under data_dir that the ledger has not seen. Safe to call repeatedly."""
    report = LoadReport()
    known = {h for (h,) in conn.execute("SELECT file_hash FROM bronze.load_ledger")}
    names = {n for (n,) in conn.execute(
        "SELECT source_file FROM bronze.load_ledger WHERE status = 'loaded'")}

    for spec in SPECS:
        for path in sorted(data_dir.glob(spec.glob)):
            report.files_seen += 1
            rel = path.relative_to(data_dir).as_posix()
            digest = file_sha256(path)
            if digest in known:
                report.files_skipped += 1
                continue
            if rel in names:  # same name, new content: load it as a NEW batch, but say so
                report.files_changed += 1
                log.warning("file changed since last load, loading as a new batch: %s", rel)
            status, loaded, rejected = load_file(conn, spec, path, rel, digest)
            known.add(digest)
            if status == "loaded":
                report.files_loaded += 1
                report.rows_loaded[spec.table] = report.rows_loaded.get(spec.table, 0) + loaded
            else:
                report.files_rejected += 1
                log.error("file rejected (bad header): %s", rel)
            report.rows_rejected += rejected
    return report


def apply_ddl(conn: psycopg.Connection) -> None:
    run_sql_dir(conn, "bronze")


def reset_bronze(conn: psycopg.Connection) -> None:
    """Forget everything that was loaded (used to start over). Keeps the table definitions."""
    tables = ", ".join(f"bronze.{t}" for t in BRONZE_TABLES)
    conn.execute(f"TRUNCATE {tables}, bronze.load_ledger, bronze.rejected_files_rows")


# ---------------------------------------------------------------- command line ----------
def _print(title: str, r: LoadReport) -> None:
    print(f"{title}: {r.files_seen} files seen, {r.files_loaded} loaded, "
          f"{r.files_skipped} skipped, {r.files_rejected} rejected, "
          f"{r.rows_rejected} bad rows, {r.total_rows:,} rows loaded")
    for table, n in r.rows_loaded.items():
        print(f"    bronze.{table:<20} +{n:,}")
    if r.files_changed:
        print(f"    note: {r.files_changed} file(s) changed since last load "
              "(loaded as new batches)")


def main() -> int:
    setup_logging()
    p = argparse.ArgumentParser()
    p.add_argument("--data-dir", type=Path, default=Path("data/raw"))
    p.add_argument("--reset", action="store_true", help="forget all loaded data first")
    p.add_argument("--rerun-test", action="store_true",
                   help="load twice and fail unless the second run loads nothing")
    a = p.parse_args()

    if not a.data_dir.exists():
        print(f"{a.data_dir} not found. Run `make data` first.")
        return 1
    with connect("etl", autocommit=True) as conn:
        apply_ddl(conn)
        if a.reset:
            reset_bronze(conn)
            print("bronze reset")
        first = load_directory(conn, a.data_dir)
        _print("run 1", first)
        if not a.rerun_test:
            return 0
        second = load_directory(conn, a.data_dir)
        _print("run 2", second)
        ok = second.files_loaded == 0 and second.total_rows == 0 and second.files_rejected == 0
        print("IDEMPOTENT: second run loaded nothing" if ok else "FAIL: second run loaded rows")
        return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
