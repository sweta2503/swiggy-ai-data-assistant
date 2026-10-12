"""Silver runner: clean bronze into typed, deduplicated tables, incrementally.

How "incremental" works
  ops.pipeline_state remembers, per bronze table, the last _batch_id silver has processed.
  Each run only reads batches after that mark, then moves the mark forward. The whole run is
  ONE transaction, so a failure leaves silver and the marks exactly as they were.
"""
import argparse
import logging
import sys
from dataclasses import dataclass, field

import psycopg

from swiggy_ai.db import connect
from swiggy_ai.logging_setup import setup_logging
from swiggy_ai.pipeline.runner import SQL_ROOT, analyze_schema, run_sql_dir, should_analyze

log = logging.getLogger("silver")
STAGE = "silver"
# bronze table -> its silver table (same order the SQL files process them)
TABLES = {
    "restaurants": "restaurants", "delivery_partners": "delivery_partners",
    "menu_items": "menu_items", "customer_snapshots": "customer_snapshots",
    "orders": "orders", "order_items": "order_items", "payments": "payments",
    "refunds": "refunds", "ratings": "ratings",
}


@dataclass
class SilverReport:
    new_batches: dict[str, int] = field(default_factory=dict)  # bronze table -> batches read
    silver_rows: dict[str, int] = field(default_factory=dict)  # silver table -> rows now
    rejected_by_reason: dict[tuple[str, str], int] = field(default_factory=dict)

    @property
    def batches_processed(self) -> int:
        return sum(self.new_batches.values())


def _count_batches(conn: psycopg.Connection, table: str, lo: int, hi: int) -> int:
    return conn.execute(
        f"SELECT count(DISTINCT _batch_id) FROM bronze.{table} WHERE _batch_id > %s"
        " AND _batch_id <= %s", (lo, hi)).fetchone()[0]


def reset_silver(conn: psycopg.Connection) -> None:
    """Forget silver and its progress marks (keeps table definitions and the status map)."""
    tables = ", ".join(f"silver.{t}" for t in TABLES.values())
    conn.execute(f"TRUNCATE {tables}, silver.rejected_rows RESTART IDENTITY")
    conn.execute("DELETE FROM ops.pipeline_state WHERE stage = %s", (STAGE,))


def run_silver(conn: psycopg.Connection, full: bool = False,
               analyze: bool | None = None) -> SilverReport:
    report = SilverReport()
    analyze = should_analyze(conn, analyze)  # decided before our own transaction begins
    with conn.transaction():
        run_sql_dir(conn, "ops")
        conn.execute((SQL_ROOT / "silver" / "00_functions.sql").read_text())
        conn.execute((SQL_ROOT / "silver" / "01_tables.sql").read_text())
        if full:
            reset_silver(conn)

        # Freeze the window now so batches that land mid-run wait for the next run.
        conn.execute("DROP TABLE IF EXISTS _window")
        conn.execute("CREATE TEMP TABLE _window (tbl text PRIMARY KEY, lo bigint, hi bigint)")
        marks = {}
        for table in TABLES:
            lo = conn.execute(
                "SELECT last_batch_id FROM ops.pipeline_state WHERE stage = %s AND table_name = %s",
                (STAGE, table)).fetchone()
            lo = lo[0] if lo else 0
            newest = conn.execute(
                f"SELECT coalesce(max(_batch_id), 0) FROM bronze.{table}").fetchone()[0]
            hi = max(newest, lo)
            marks[table] = hi
            conn.execute("INSERT INTO _window VALUES (%s, %s, %s)", (table, lo, hi))
            report.new_batches[table] = _count_batches(conn, table, lo, hi)

        for path in sorted((SQL_ROOT / "silver").glob("*.sql")):
            if path.name in ("00_functions.sql", "01_tables.sql"):
                continue
            conn.execute(path.read_text())
            if analyze:  # later steps join to the tables this one just filled
                analyze_schema(conn, "silver")

        for table, hi in marks.items():
            conn.execute(
                "INSERT INTO ops.pipeline_state (stage, table_name, last_batch_id)"
                " VALUES (%s, %s, %s) ON CONFLICT (stage, table_name)"
                " DO UPDATE SET last_batch_id = EXCLUDED.last_batch_id, updated_at = now()",
                (STAGE, table, hi))
        for silver_table in TABLES.values():
            report.silver_rows[silver_table] = conn.execute(
                f"SELECT count(*) FROM silver.{silver_table}").fetchone()[0]
        report.rejected_by_reason = {
            (t, r): n for t, r, n in conn.execute(
                "SELECT table_name, reason, count(*) FROM silver.rejected_rows"
                " GROUP BY 1, 2 ORDER BY 1, 2")}
    return report


def main() -> int:
    setup_logging()
    p = argparse.ArgumentParser()
    p.add_argument("--full", action="store_true", help="rebuild silver from all of bronze")
    a = p.parse_args()
    with connect("etl", autocommit=True) as conn:
        r = run_silver(conn, full=a.full)
    print(f"silver {'full rebuild' if a.full else 'incremental'}: "
          f"{r.batches_processed} new bronze batches processed")
    for table, n in r.silver_rows.items():
        print(f"    silver.{table:<20} {n:>10,} rows")
    if r.rejected_by_reason:
        print("  rejected rows (kept in silver.rejected_rows):")
        for (table, reason), n in r.rejected_by_reason.items():
            print(f"    {table:<20} {reason:<32} {n:>6,}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
