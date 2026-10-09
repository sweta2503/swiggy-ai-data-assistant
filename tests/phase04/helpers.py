"""Shared by the phase 4 test files."""
import json
from datetime import date
from pathlib import Path

import psycopg
import pytest

from swiggy_ai.datagen.config import GenConfig
from swiggy_ai.datagen.generate import generate
from swiggy_ai.db import connect
from swiggy_ai.pipeline import bronze_loader as bl
from swiggy_ai.pipeline import gold as gd
from swiggy_ai.pipeline import silver as sv


def make_raw(out: Path) -> Path:
    generate(GenConfig(seed=42, out_dir=out, end_date=date(2026, 9, 30), days=60, n_orders=4000,
                       n_customers=700, n_restaurants=90, n_partners=120))
    return out


def fresh_connection() -> psycopg.Connection:
    c = connect("etl")
    bl.apply_ddl(c)
    bl.reset_bronze(c)
    sv.run_silver(c, full=True)  # creates silver tables, empties them
    gd.run_gold(c, full=True)  # creates gold tables, empties them
    return c


def build(c: psycopg.Connection, data_dir: Path) -> None:
    bl.load_directory(c, data_dir)
    sv.run_silver(c)
    gd.run_gold(c, data_dir=data_dir)


def one(conn, sql, *args):
    return conn.execute(sql, args).fetchone()[0]


def q(conn, sql, *args):
    return conn.execute(sql, args).fetchall()


def read_json(path: Path) -> dict:
    return json.loads(path.read_text())


requires_db = pytest.mark.db
