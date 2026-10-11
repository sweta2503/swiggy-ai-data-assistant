"""The response cache: remember answers until the data changes.

Safety rules, because a wrong cached answer is worse than a slow one:
  * the key contains the DATA VERSION, so a published data change retires every cached answer;
  * the key contains the CITY SCOPE, so a Mumbai manager never gets the all-cities answer;
  * only the first question of a conversation is cached ("and in Pune?" means nothing alone);
  * only clean answers are stored (no warnings, no injected fault, nothing that looks personal).
"""
import hashlib
import re
import unicodedata
from datetime import datetime
from typing import Any

import psycopg
from psycopg.types.json import Jsonb

from swiggy_ai.pipeline.runner import run_sql_file


def ensure_cache_tables(conn: psycopg.Connection) -> None:
    run_sql_file(conn, "ops", "50_pipeline_runs.sql")
    run_sql_file(conn, "ops", "60_answer_cache.sql")


def normalize(question: str) -> str:
    """Same question, different typing: case, spacing and trailing punctuation do not matter."""
    q = unicodedata.normalize("NFKC", question or "").lower()
    return re.sub(r"\s+", " ", q).strip(" \t\n?!.;,")


def scope_hash(allowed_cities: str) -> str:
    text = (allowed_cities or "").strip()
    if text.upper() == "ALL":
        return "ALL"
    cities = sorted({c.strip().lower() for c in text.split(",") if c.strip()})
    return hashlib.sha256(",".join(cities).encode()).hexdigest()[:16]


def cache_key(question_norm: str, data_version: int, scope: str) -> str:
    return hashlib.sha256(f"{question_norm}\x1f{data_version}\x1f{scope}".encode()).hexdigest()


def get_data_version(conn: psycopg.Connection) -> int:
    return conn.execute("SELECT version FROM ops.data_version WHERE id = 1").fetchone()[0]


def bump_data_version(conn: psycopg.Connection, run_id: Any = None) -> int:
    """The data changed and was published. Retire every answer computed on the old data."""
    version = conn.execute(
        "UPDATE ops.data_version SET version = version + 1, updated_at = now(), run_id = %s"
        " WHERE id = 1 RETURNING version", (run_id,)).fetchone()[0]
    conn.execute("DELETE FROM ops.answer_cache WHERE data_version < %s", (version,))
    return version


def lookup(conn: psycopg.Connection, key: str) -> tuple[dict, datetime] | None:
    row = conn.execute(
        "UPDATE ops.answer_cache SET hits = hits + 1, last_hit_at = now() WHERE cache_key = %s"
        " RETURNING answer_json, created_at", (key,)).fetchone()
    return (row[0], row[1]) if row else None


def store(conn: psycopg.Connection, key: str, question_norm: str, data_version: int,
          scope: str, answer_json: dict) -> None:
    conn.execute(
        "INSERT INTO ops.answer_cache (cache_key, question_norm, data_version, scope_hash,"
        " answer_json) VALUES (%s, %s, %s, %s, %s) ON CONFLICT (cache_key) DO UPDATE SET"
        " answer_json = EXCLUDED.answer_json, created_at = now(), hits = 0, last_hit_at = NULL",
        (key, question_norm, data_version, scope, Jsonb(answer_json)))


def clear(conn: psycopg.Connection) -> int:
    return conn.execute("DELETE FROM ops.answer_cache").rowcount


def stats(conn: psycopg.Connection) -> dict[str, int]:
    entries, hits = conn.execute(
        "SELECT count(*), coalesce(sum(hits), 0) FROM ops.answer_cache").fetchone()
    return {"entries": entries, "hits": int(hits), "data_version": get_data_version(conn)}
