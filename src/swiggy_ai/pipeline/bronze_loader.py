"""Bronze loader: land source files in the database exactly as received.

Rules that make it safe to run any number of times:
  1. A file is identified by the sha256 of its CONTENT, not its name.
  2. Hash already in `bronze.load_ledger`  ->  skip (idempotent).
  3. Each file loads in ONE transaction together with its ledger row, so a crash
     leaves either everything or nothing for that file.
  4. Unparseable rows go to `bronze.rejected_files_rows`; they are never silently dropped.
  5. A file whose columns differ from the expected ones (schema drift) is quarantined, with an
     alert, unless the owner approved the change in `column_aliases.yml`. See schema_drift.py.
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
from swiggy_ai.pipeline import schema_drift as drift
from swiggy_ai.pipeline.runner import run_sql_dir, run_sql_file, should_analyze

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
    files_quarantined: int = 0  # schema drift: parked, not loaded
    files_released: int = 0  # previously quarantined, now loaded because the change was approved
    rows_loaded: dict[str, int] = field(default_factory=dict)
    rows_rejected: int = 0

    @property
    def total_rows(self) -> int:
        return sum(self.rows_loaded.values())


def file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


# ---------------------------------------------------------------- parsing ----------------
@dataclass
class Parsed:
    columns: list[str]  # the columns the FILE has, in file order
    rows: list[tuple[int, dict[str, str | None]]]  # (row number, column name -> value)
    rejects: list[tuple[int, str, str]]  # (row number, raw text, why)


def parse_csv(path: Path) -> Parsed:
    with path.open(newline="") as f:
        reader = csv.reader(f)
        header = next(reader, [])
        rows, rejects = [], []
        for n, rec in enumerate(reader, start=1):  # n counts records, not lines (fields can wrap)
            if len(rec) != len(header):
                rejects.append((n, json.dumps(rec), f"expected {len(header)} fields, "
                                f"got {len(rec)}"))
            else:
                rows.append((n, dict(zip(header, rec, strict=True))))  # blanks stay ''
    return Parsed(header, rows, rejects)


def parse_jsonl(path: Path) -> Parsed:
    rows, rejects, columns = [], [], {}
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
        columns.update(dict.fromkeys(rec))  # the union of keys, in first-seen order
        # JSON null -> SQL NULL; everything else becomes text
        rows.append((n, {k: None if v is None else str(v) for k, v in rec.items()}))
    return Parsed(list(columns), rows, rejects)


def parse(spec: FileSpec, path: Path) -> Parsed:
    return parse_csv(path) if spec.fmt == "csv" else parse_jsonl(path)


# ---------------------------------------------------------------- loading ----------------
def load_file(conn: psycopg.Connection, spec: FileSpec, path: Path, rel_name: str,
              file_hash: str, parsed: Parsed | None = None,
              renames: dict[str, str] | None = None) -> tuple[str, int, int]:
    """Load one file inside a transaction. `renames` maps names in the file to the expected ones
    (approved schema changes). Returns (status, rows_loaded, rows_rejected)."""
    parsed = parsed or parse(spec, path)
    source_name = {new: old for old, new in (renames or {}).items()}  # expected -> name in file
    ingested_at = datetime.now(UTC)

    with conn.transaction():
        batch_id = conn.execute("SELECT nextval('bronze.batch_seq')").fetchone()[0]
        cols = ", ".join((*spec.columns, *META_COLUMNS))
        copy_sql = f"COPY bronze.{spec.table} ({cols}) FROM STDIN"
        with conn.cursor() as cur, cur.copy(copy_sql) as cp:
            for n, rec in parsed.rows:
                values = [rec.get(source_name.get(c, c)) for c in spec.columns]
                cp.write_row((*values, rel_name, file_hash, ingested_at, batch_id, n))
        if parsed.rejects:
            with conn.cursor() as cur:
                cur.executemany(
                    "INSERT INTO bronze.rejected_files_rows"
                    "(source_file, file_hash, batch_id, row_number, raw_content, reason)"
                    " VALUES (%s, %s, %s, %s, %s, %s)",
                    [(rel_name, file_hash, batch_id, n, raw, why)
                     for n, raw, why in parsed.rejects])
        conn.execute(
            "INSERT INTO bronze.load_ledger"
            "(file_hash, source_file, row_count, rejected_count, batch_id, status)"
            " VALUES (%s, %s, %s, %s, %s, %s)",
            (file_hash, rel_name, len(parsed.rows), len(parsed.rejects), batch_id, "loaded"))
    return "loaded", len(parsed.rows), len(parsed.rejects)


def load_directory(conn: psycopg.Connection, data_dir: Path,
                   aliases: drift.Aliases | None = None, analyze: bool | None = None) -> LoadReport:
    """Load every file under data_dir that the ledger has not seen. Safe to call repeatedly.
    Files whose columns changed are quarantined unless `aliases` (default: column_aliases.yml)
    approves the change."""
    report = LoadReport()
    analyze = should_analyze(conn, analyze)
    aliases = aliases if aliases is not None else drift.load_aliases()
    drift.seed_expected(conn, SPECS)
    known = {h for (h,) in conn.execute("SELECT file_hash FROM bronze.load_ledger")}
    names = {n for (n,) in conn.execute(
        "SELECT source_file FROM bronze.load_ledger WHERE status = 'loaded'")}
    parked = {h for (h,) in conn.execute(
        "SELECT file_hash FROM bronze.quarantine_files WHERE status = 'open'")}

    for spec in SPECS:
        expected = drift.expected_columns(conn, spec.table)
        for path in sorted(data_dir.glob(spec.glob)):
            report.files_seen += 1
            rel = path.relative_to(data_dir).as_posix()
            digest = file_sha256(path)
            if digest in known:
                report.files_skipped += 1
                continue
            parsed = parse(spec, path)
            diff = drift.compare(spec.table, expected, parsed.columns, aliases)
            if diff.status == "drift":
                report.files_quarantined += 1
                if digest not in parked:  # seen for the first time: park it and raise the alert
                    drift.quarantine(
                        conn, file_type=spec.table, rel_name=rel, file_hash=digest,
                        observed=parsed.columns, expected=expected, diff=diff,
                        records=[(n, dict(r)) for n, r in parsed.rows])
                    parked.add(digest)
                    log.error("SCHEMA DRIFT in %s (%s): quarantined, nothing loaded", rel,
                              diff.describe())
                continue
            if diff.status == "approved":
                log.warning("approved schema change in %s: renames %s, ignored new columns %s",
                            rel, diff.renames, diff.ignored)
            if rel in names:  # same name, new content: load it as a NEW batch, but say so
                report.files_changed += 1
                log.warning("file changed since last load, loading as a new batch: %s", rel)
            _, loaded, rejected = load_file(conn, spec, path, rel, digest, parsed, diff.renames)
            known.add(digest)
            report.files_loaded += 1
            report.rows_loaded[spec.table] = report.rows_loaded.get(spec.table, 0) + loaded
            report.rows_rejected += rejected
            if digest in parked:
                how = (f"approved in column_aliases.yml: renames {diff.renames}, "
                       f"ignored new columns {diff.ignored}")
                drift.release(conn, digest, how)
                parked.discard(digest)
                report.files_released += 1
    for table in report.rows_loaded if analyze else ():  # a fresh bulk load has no statistics yet
        conn.execute(f"ANALYZE bronze.{table}")
    return report


def apply_ddl(conn: psycopg.Connection) -> None:
    run_sql_dir(conn, "bronze")
    run_sql_file(conn, "ops", "40_schema_drift.sql")  # the expected schemas and the alerts


def reset_bronze(conn: psycopg.Connection) -> None:
    """Forget everything that was loaded (used to start over). Keeps the table definitions."""
    tables = ", ".join(f"bronze.{t}" for t in BRONZE_TABLES)
    conn.execute(f"TRUNCATE {tables}, bronze.load_ledger, bronze.rejected_files_rows, "
                 "bronze.quarantine_files, bronze.quarantine_rows")


# ---------------------------------------------------------------- command line ----------
def _print(title: str, r: LoadReport) -> None:
    print(f"{title}: {r.files_seen} files seen, {r.files_loaded} loaded, "
          f"{r.files_skipped} skipped, {r.files_quarantined} quarantined, "
          f"{r.rows_rejected} bad rows, {r.total_rows:,} rows loaded")
    for table, n in r.rows_loaded.items():
        print(f"    bronze.{table:<20} +{n:,}")
    if r.files_quarantined:
        print(f"    !! {r.files_quarantined} file(s) have a changed schema and were NOT loaded. "
              "See ops.schema_alerts (make drift-demo shows the whole story).")
    if r.files_released:
        print(f"    {r.files_released} quarantined file(s) released after approval")
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
        ok = second.files_loaded == 0 and second.total_rows == 0
        print("IDEMPOTENT: second run loaded nothing" if ok else "FAIL: second run loaded rows")
        return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
