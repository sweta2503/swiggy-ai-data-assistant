"""Phase 7, part 4: the whole path with real logins against the committed dev data.

Kept in its own module on purpose: the other security tests hold locks inside open transactions
while they run, and these tests read the same tables through separate connections.
"""
import pytest

from swiggy_ai.db import connect
from swiggy_ai.security.scope import (
    assistant_connection,
    connection_for,
    lookup_user,
    new_api_key,
    seed_user,
)

pytestmark = pytest.mark.db


def test_end_to_end_with_real_logins_and_the_real_database():
    """Key -> user -> scoped assistant login -> rows. Uses the committed dev data."""
    with connect("etl", autocommit=True) as etl:
        total = etl.execute("SELECT count(*) FROM gold.fact_orders").fetchone()[0]
        if total == 0:
            pytest.skip("the dev database has no data; run the pipeline first")
        blr = etl.execute("SELECT count(*) FROM gold.fact_orders f JOIN gold.dim_city c USING"
                          " (city_sk) WHERE c.city_name = 'Bengaluru'").fetchone()[0]
        key = new_api_key()
        etl.execute("DELETE FROM ops.app_users WHERE user_id = 'e2e_test'")
        seed_user(etl, "e2e_test", "End to end", key, "Bengaluru")
        try:
            user = lookup_user(etl, key)
            with connection_for(user) as c:
                assert c.execute("SELECT count(*) FROM analytics.orders").fetchone()[0] == blr
                assert c.execute("SELECT count(DISTINCT city) FROM analytics.orders"
                                 ).fetchone()[0] == 1
            with assistant_connection("ALL") as c:
                assert c.execute("SELECT count(*) FROM analytics.orders").fetchone()[0] == total
        finally:
            etl.execute("DELETE FROM ops.app_users WHERE user_id = 'e2e_test'")


def test_the_scope_value_is_quoted_so_it_cannot_break_out_of_the_set_statement():
    with assistant_connection("Bengaluru'; DROP TABLE x; --") as c:
        assert c.execute("SHOW app.allowed_cities").fetchone()[0] == "Bengaluru'; DROP TABLE x; --"
        assert c.execute("SELECT count(*) FROM analytics.orders").fetchone()[0] == 0
