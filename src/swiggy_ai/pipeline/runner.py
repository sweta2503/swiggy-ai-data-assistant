"""Tiny helpers shared by every pipeline stage."""
from pathlib import Path

import psycopg
from psycopg import sql
from psycopg.pq import TransactionStatus

SQL_ROOT = Path(__file__).resolve().parents[3] / "sql"


def run_sql_dir(conn: psycopg.Connection, folder: str) -> list[str]:
    """Run every .sql file in sql/<folder> in name order. Files are written to be re-runnable."""
    ran = []
    for path in sorted((SQL_ROOT / folder).glob("*.sql")):
        conn.execute(path.read_text())
        ran.append(path.name)
    return ran


def run_sql_file(conn: psycopg.Connection, folder: str, name: str) -> None:
    conn.execute((SQL_ROOT / folder / name).read_text())


def should_analyze(conn: psycopg.Connection, requested: bool | None) -> bool:
    """Analyse only when the pipeline owns its transaction.

    ANALYZE writes each table's row count in place, and that write survives a ROLLBACK. If a caller
    has a transaction open (a test, a dry run) and later rolls it back, the row counts of the
    rolled-back load would stay behind and mislead the planner. A normal run uses an autocommit
    connection, so the pipeline's own transaction commits and its statistics are right."""
    if requested is not None:
        return requested
    return conn.info.transaction_status == TransactionStatus.IDLE


def analyze_schema(conn: psycopg.Connection, schema: str) -> None:
    """Refresh the planner's statistics for every table in a schema.

    Tables built inside one transaction have no statistics yet (autovacuum cannot see uncommitted
    work), so the planner assumes they are tiny and picks nested loops that never finish on a
    first run with real volumes. Analysing after each load step keeps the plans sane. Safe inside
    a transaction."""
    tables = [t for (t,) in conn.execute(
        "SELECT tablename FROM pg_tables WHERE schemaname = %s ORDER BY 1", (schema,))]
    for t in tables:
        conn.execute(sql.SQL("ANALYZE {}.{}").format(sql.Identifier(schema), sql.Identifier(t)))
