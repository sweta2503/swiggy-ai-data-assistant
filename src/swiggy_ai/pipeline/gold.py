"""Gold runner: build the star schema from silver.

Re-runnable: dimensions are upserts on natural keys (surrogate keys never change), facts load
only rows that are new or changed in silver, and the aggregate is rebuilt from the facts.
The whole run is one transaction.
"""
import argparse
import json
import logging
import sys
from dataclasses import dataclass, field
from pathlib import Path

import psycopg

from swiggy_ai.db import connect
from swiggy_ai.logging_setup import setup_logging
from swiggy_ai.pipeline.runner import SQL_ROOT, analyze_schema, run_sql_dir, should_analyze

log = logging.getLogger("gold")
TABLES = [
    "dim_date", "dim_city", "dim_restaurant", "dim_partner", "dim_customer", "customer_pii",
    "fact_orders", "fact_order_items", "fact_refunds", "daily_city_sales",
]


@dataclass
class GoldReport:
    rows: dict[str, int] = field(default_factory=dict)
    added: dict[str, int] = field(default_factory=dict)  # rows after minus rows before


def _count(conn: psycopg.Connection, table: str) -> int:
    return conn.execute(f"SELECT count(*) FROM gold.{table}").fetchone()[0]


def seed_festivals(conn: psycopg.Connection, data_dir: Path | None) -> int:
    """The festival calendar is reference data owned by the business, not derived from orders.
    For the demo it comes from the generator's _meta.json; in real life it would be a table
    someone maintains."""
    meta = data_dir / "_meta.json" if data_dir else None
    if not meta or not meta.exists():
        return 0
    dates = json.loads(meta.read_text()).get("festival_dates", [])
    for i, d in enumerate(dates, start=1):
        conn.execute("INSERT INTO gold.festival_calendar VALUES (%s, %s)"
                     " ON CONFLICT (festival_date) DO NOTHING", (d, f"Festival {i}"))
    return len(dates)


def apply_security_objects(conn: psycopg.Connection) -> None:
    """(Re)create what protects gold: the access tables, the row-level security rules and the
    analytics views the assistant reads. Run as the table owner. Safe to repeat."""
    run_sql_dir(conn, "ops")
    conn.execute((SQL_ROOT / "gold" / "00_tables.sql").read_text())
    conn.execute((SQL_ROOT / "security" / "05_rls.sql").read_text())
    conn.execute((SQL_ROOT / "security" / "10_analytics_views.sql").read_text())


apply_views = apply_security_objects  # the old name, kept so earlier chapters still read well


def reset_gold(conn: psycopg.Connection) -> None:
    """Empty gold (keeps the table definitions). Surrogate keys restart from 1."""
    conn.execute("TRUNCATE gold.fact_refunds, gold.fact_order_items, gold.fact_orders,"
                 " gold.daily_city_sales, gold.customer_pii, gold.dim_customer,"
                 " gold.dim_partner, gold.dim_restaurant, gold.dim_city, gold.dim_date,"
                 " gold.festival_calendar RESTART IDENTITY")


def run_gold(conn: psycopg.Connection, full: bool = False, data_dir: Path | None = None,
             analyze: bool | None = None) -> GoldReport:
    report = GoldReport()
    analyze = should_analyze(conn, analyze)  # decided before our own transaction begins
    with conn.transaction():
        conn.execute((SQL_ROOT / "gold" / "00_tables.sql").read_text())
        if full:
            reset_gold(conn)
        before = {t: _count(conn, t) for t in TABLES}
        seed_festivals(conn, data_dir)
        for path in sorted((SQL_ROOT / "gold").glob("*.sql")):
            if path.name != "00_tables.sql":
                conn.execute(path.read_text())
                if analyze:  # the next file joins to what this one just loaded
                    analyze_schema(conn, "gold")
        apply_security_objects(conn)
        for t in TABLES:
            report.rows[t] = _count(conn, t)
            report.added[t] = report.rows[t] - before[t]
    return report


def main() -> int:
    setup_logging()
    p = argparse.ArgumentParser()
    p.add_argument("--full", action="store_true", help="empty gold and rebuild from silver")
    p.add_argument("--data-dir", type=Path, default=Path("data/raw"))
    a = p.parse_args()
    with connect("etl", autocommit=True) as conn:
        r = run_gold(conn, full=a.full, data_dir=a.data_dir)
    print(f"gold {'full rebuild' if a.full else 'incremental'}:")
    for t in TABLES:
        print(f"    gold.{t:<20} {r.rows[t]:>10,} rows  ({r.added[t]:+,})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
