"""Helpers for the security tests.

Most tests build a small dataset inside ONE admin transaction, then act as another role with
`SET LOCAL SESSION AUTHORIZATION` (which changes both current_user and session_user, exactly as a
real login would). Everything is rolled back at the end.
"""
from contextlib import contextmanager
from pathlib import Path

import psycopg

from swiggy_ai.db import connect
from swiggy_ai.pipeline import bronze_loader as bl
from swiggy_ai.pipeline import gold as gd
from swiggy_ai.pipeline import silver as sv


def admin_connection_with_data(raw: Path):
    c = connect("admin")
    bl.apply_ddl(c)
    bl.reset_bronze(c)
    sv.run_silver(c, full=True)
    gd.run_gold(c, full=True)
    bl.load_directory(c, raw)
    sv.run_silver(c)
    gd.run_gold(c, data_dir=raw)
    return c


@contextmanager
def acting_as(conn, role: str, scope: str | None = None):
    """Act as `role` (optionally with a declared city scope) for the length of the block.

    The block runs inside a savepoint that is always rolled back, so a statement that fails on
    purpose cannot leave the connection in an aborted state, and nothing leaks out of the block.
    """
    try:
        with conn.transaction():
            conn.execute(f'SET LOCAL SESSION AUTHORIZATION "{role}"')
            if scope is not None:
                conn.execute(f"SET app.allowed_cities = '{scope}'")
            yield conn
            raise psycopg.Rollback()
    except psycopg.Rollback:
        pass
