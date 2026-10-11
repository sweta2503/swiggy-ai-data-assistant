"""Tiny helpers shared by every pipeline stage."""
from pathlib import Path

import psycopg

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
