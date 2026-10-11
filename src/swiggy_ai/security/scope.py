"""Who is asking, and which cities may they see?

The flow for every question:
    API key --(hash)--> ops.app_users --> allowed cities --> assistant_connection(scope)
The connection then declares its scope to the database (SET app.allowed_cities), and
row-level security does the rest.
"""
import hashlib
import secrets
from dataclasses import dataclass

import psycopg
from psycopg import sql

from swiggy_ai.db import connect

MIN_KEY_LENGTH = 16


class ScopeError(ValueError):
    """The user, key or city list is not acceptable."""


@dataclass(frozen=True)
class AppUser:
    user_id: str
    display_name: str
    allowed_cities: str  # 'ALL' or a comma-separated list of exact city names

    @property
    def sees_everything(self) -> bool:
        return self.allowed_cities.upper() == "ALL"


def hash_key(api_key: str) -> str:
    """Only this hash is stored. API keys are long random strings, so a plain SHA-256 is enough
    (unlike passwords, they cannot be guessed from a dictionary)."""
    return hashlib.sha256(api_key.encode()).hexdigest()


def new_api_key() -> str:
    return secrets.token_urlsafe(24)


def known_cities(conn: psycopg.Connection) -> set[str]:
    return {c for (c,) in conn.execute("SELECT city_name FROM gold.dim_city WHERE city_sk > 0")}


def normalise_scope(conn: psycopg.Connection, allowed: str) -> str:
    """'all' -> 'ALL'; 'pune, mumbai' -> 'Mumbai,Pune' (exact names only). Anything unknown is an
    error, so a typo can never silently widen or empty a user's view."""
    text = (allowed or "").strip()
    if not text:
        raise ScopeError("allowed_cities is empty")
    if text.upper() == "ALL":
        return "ALL"
    cities = known_cities(conn)
    by_lower = {c.lower(): c for c in cities}
    chosen = []
    for part in text.split(","):
        name = by_lower.get(part.strip().lower())
        if name is None:
            raise ScopeError(f"unknown city '{part.strip()}'. Known: {sorted(cities)}")
        chosen.append(name)
    return ",".join(sorted(set(chosen)))


def seed_user(conn: psycopg.Connection, user_id: str, display_name: str, api_key: str,
              allowed_cities: str) -> AppUser:
    """Create or update a user. The key is hashed before it touches the database."""
    if len(api_key) < MIN_KEY_LENGTH:
        raise ScopeError(f"API key for {user_id} is shorter than {MIN_KEY_LENGTH} characters")
    scope = normalise_scope(conn, allowed_cities)
    conn.execute(
        "INSERT INTO ops.app_users (user_id, display_name, api_key_hash, allowed_cities)"
        " VALUES (%s, %s, %s, %s) ON CONFLICT (user_id) DO UPDATE SET"
        " display_name = EXCLUDED.display_name, api_key_hash = EXCLUDED.api_key_hash,"
        " allowed_cities = EXCLUDED.allowed_cities, is_active = true",
        (user_id, display_name, hash_key(api_key), scope))
    return AppUser(user_id, display_name, scope)


def lookup_user(conn: psycopg.Connection, api_key: str | None) -> AppUser | None:
    """The user behind an API key, or None (unknown key, inactive user, empty key)."""
    if not api_key:
        return None
    row = conn.execute(
        "SELECT user_id, display_name, allowed_cities FROM ops.app_users"
        " WHERE api_key_hash = %s AND is_active", (hash_key(api_key),)).fetchone()
    return AppUser(*row) if row else None


def assistant_connection(scope: str) -> psycopg.Connection:
    """A login as assistant_ro that has declared its city scope. The role can only read the
    analytics views; the scope decides which cities' rows those views return."""
    conn = connect("assistant", autocommit=True)
    # SET takes a literal, not a bind parameter, so quote it properly.
    conn.execute(sql.SQL("SET app.allowed_cities = {}").format(sql.Literal(scope)))
    return conn


def connection_for(user: AppUser) -> psycopg.Connection:
    return assistant_connection(user.allowed_cities)
