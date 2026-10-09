"""One connection helper per database role.

Three roles on purpose (least privilege):
  admin     - creates schemas/roles, used by `make` setup commands
  etl       - writes bronze/silver/gold
  assistant - read-only, can only see the `analytics` views
"""
from typing import Literal

import psycopg

from swiggy_ai.config import get_settings

Role = Literal["admin", "etl", "assistant"]


def _url(role: Role) -> str:
    s = get_settings()
    return {
        "admin": s.database_url_admin,
        "etl": s.database_url_etl,
        "assistant": s.database_url_assistant,
    }[role]


def connect(role: Role = "etl", autocommit: bool = False) -> psycopg.Connection:
    return psycopg.connect(_url(role), autocommit=autocommit, connect_timeout=5)
