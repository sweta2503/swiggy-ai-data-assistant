"""`make security`, `make seed-users`, `make security-demo`."""
import argparse
import sys
import time

import psycopg

from swiggy_ai.config import get_settings
from swiggy_ai.db import connect
from swiggy_ai.logging_setup import setup_logging
from swiggy_ai.security.scope import (
    ScopeError,
    assistant_connection,
    seed_user,
)
from swiggy_ai.security.setup import apply_security, verify_security

DEMO_USERS = [  # user_id, display name, which setting holds the key, cities
    ("admin", "Admin (all cities)", "api_key", "ALL"),
    ("bengaluru_manager", "Bengaluru manager", "api_key_bengaluru", "Bengaluru"),
    ("mumbai_manager", "Mumbai manager", "api_key_mumbai", "Mumbai"),
]


def seed_demo_users() -> int:
    settings = get_settings()
    with connect("etl", autocommit=True) as conn:
        for user_id, name, attr, cities in DEMO_USERS:
            key = getattr(settings, attr)
            try:
                seed_user(conn, user_id, name, key, cities)
                print(f"  user {user_id:<18} cities={cities}")
            except ScopeError as e:
                print(f"  skipped {user_id}: {e}")
                return 1
            if key.startswith("change-me"):
                print(f"    note: {attr.upper()} is still the example value. Change it in .env.")
    return 0


def demo() -> int:
    """Show the layers working, using real logins as the assistant."""
    print("Logged in as the assistant (assistant_ro):")

    def attempt(label: str, scope: str, statement: str) -> None:
        with assistant_connection(scope) as c:
            try:
                rows = c.execute(statement).fetchall()
                print(f"  [allowed] {label}: {rows[0][0] if rows and len(rows[0]) == 1 else rows}")
            except psycopg.Error as e:
                print(f"  [BLOCKED] {label}: {type(e).__name__}: {str(e).splitlines()[0]}")

    for table in ("bronze.orders", "silver.orders", "gold.fact_orders", "gold.customer_pii",
                  "ops.app_users"):
        attempt(f"read {table}", "ALL", f"SELECT count(*) FROM {table}")
    attempt("DELETE FROM analytics.orders", "ALL", "DELETE FROM analytics.orders")
    attempt("DROP VIEW analytics.orders", "ALL", "DROP VIEW analytics.orders")
    attempt("widen scope with set_config", "Bengaluru",
            "SELECT set_config('app.allowed_cities', 'ALL', false)")
    started = time.time()
    attempt("pg_sleep(60) (stopped by statement_timeout)", "ALL", "SELECT pg_sleep(60)")
    print(f"    ...cut off after {time.time() - started:.1f}s")
    print("\nSame question, three scopes - count of orders and net revenue:")
    q = "SELECT count(*), round(sum(net_revenue)) FROM analytics.orders"
    for scope in ("ALL", "Bengaluru", "Mumbai"):
        attempt(f"scope {scope}", scope, q)
    attempt("no scope declared (fails closed)", "", q)
    print("\nWhat the assistant sees of a customer:")
    attempt("analytics.customers (first row)", "ALL",
            "SELECT customer_id, city, segment, masked_phone FROM analytics.customers LIMIT 1")
    return 0


def main() -> int:
    setup_logging()
    p = argparse.ArgumentParser()
    p.add_argument("--verify", action="store_true", help="only check the live setup")
    p.add_argument("--seed-users", action="store_true", help="create the demo users from .env")
    p.add_argument("--demo", action="store_true", help="show the protections with real logins")
    a = p.parse_args()

    if a.seed_users:
        print("Seeding users (API keys are stored hashed):")
        return seed_demo_users()
    if a.demo:
        return demo()
    if not a.verify:
        apply_security()
        print("security applied")
    with connect("admin", autocommit=True) as admin:
        problems = verify_security(admin)
    if problems:
        print(f"security check found {len(problems)} problems:")
        for prob in problems:
            print(f"  - {prob}")
        return 1
    print("security verified: the assistant can read only the analytics views, writes are "
          "impossible, city scoping is forced, and session limits are set")
    return 0


if __name__ == "__main__":
    sys.exit(main())
