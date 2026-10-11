"""Phase 7, part 2: city scoping. A user sees only their cities, through every view, and a
session that does not declare a scope sees nothing.

Data is built in one admin transaction; roles are assumed with SET LOCAL SESSION AUTHORIZATION.
"""
import pytest

from swiggy_ai.security.setup import allowed_views
from tests.phase04.helpers import make_raw
from tests.phase07.helpers import acting_as, admin_connection_with_data

pytestmark = pytest.mark.db
CITY_VIEWS = {  # view -> the column holding the city
    "orders": "city", "order_items": "city", "refunds": "city", "customers": "city",
    "restaurants": "city", "daily_city_sales": "city",
}


@pytest.fixture(scope="module")
def raw(tmp_path_factory):
    return make_raw(tmp_path_factory.mktemp("raw"))


@pytest.fixture(scope="module")
def db(raw):
    c = admin_connection_with_data(raw)
    yield c
    c.rollback()
    c.close()


def one(conn, sql, *args):
    return conn.execute(sql, args).fetchone()[0]


def rows(conn, sql, *args):
    return conn.execute(sql, args).fetchall()


def everything(db, view):
    """What an unrestricted session (the admin) sees in a view."""
    return one(db, f"SELECT count(*) FROM analytics.{view}")


# --- a scope shows exactly that city, through every view --------------------------------------
@pytest.mark.parametrize("view", CITY_VIEWS)
def test_a_single_city_scope_returns_only_that_city_and_all_of_it(db, view):
    total_blr = one(db, f"SELECT count(*) FROM analytics.{view} WHERE city = 'Bengaluru'")
    assert total_blr > 0
    with acting_as(db, "assistant_ro", "Bengaluru"):
        assert rows(db, f"SELECT DISTINCT city FROM analytics.{view}") == [("Bengaluru",)]
        assert one(db, f"SELECT count(*) FROM analytics.{view}") == total_blr


@pytest.mark.parametrize("view", CITY_VIEWS)
def test_scope_all_sees_what_the_admin_sees(db, view):
    with acting_as(db, "assistant_ro", "ALL"):
        assert one(db, f"SELECT count(*) FROM analytics.{view}") == everything(db, view)
    with acting_as(db, "assistant_ro", "all"):  # case does not matter
        assert one(db, f"SELECT count(*) FROM analytics.{view}") == everything(db, view)


def test_two_cities_with_spaces_in_the_list(db):
    expected = one(db, "SELECT count(*) FROM analytics.orders WHERE city IN ('Mumbai', 'Pune')")
    with acting_as(db, "assistant_ro", "Mumbai , Pune"):
        assert one(db, "SELECT count(*) FROM analytics.orders") == expected
        assert {c for (c,) in rows(db, "SELECT DISTINCT city FROM analytics.orders")} == {
            "Mumbai", "Pune"}


def test_the_city_scoped_numbers_add_up_to_the_total(db):
    total = one(db, "SELECT sum(net_revenue) FROM analytics.orders")
    parts = 0
    for (city,) in rows(db, "SELECT DISTINCT city FROM analytics.orders"):
        with acting_as(db, "assistant_ro", city):
            parts += one(db, "SELECT sum(net_revenue) FROM analytics.orders")
    assert parts == total


# --- fail closed ------------------------------------------------------------------------------
@pytest.mark.parametrize("view", CITY_VIEWS)
def test_a_session_that_declares_no_scope_sees_nothing(db, view):
    with acting_as(db, "assistant_ro"):  # no scope at all
        assert one(db, f"SELECT count(*) FROM analytics.{view}") == 0


@pytest.mark.parametrize("scope", ["", "   ", ",", "Atlantis", "Bengaluru;DROP", "'", "ALL CITIES"])
def test_empty_or_unknown_scopes_see_nothing(db, scope):
    with acting_as(db, "assistant_ro", scope.replace("'", "''")):
        assert one(db, "SELECT count(*) FROM analytics.orders") == 0
        assert one(db, "SELECT count(*) FROM analytics.restaurants") == 0


def test_a_scope_naming_one_real_and_one_fake_city_shows_only_the_real_one(db):
    with acting_as(db, "assistant_ro", "Pune,Atlantis"):
        assert rows(db, "SELECT DISTINCT city FROM analytics.orders") == [("Pune",)]


# --- the scenario from the evaluation set -----------------------------------------------------
def test_a_bengaluru_manager_asking_for_all_city_revenue_gets_only_bengaluru(db):
    everywhere = one(db, "SELECT sum(net_revenue) FROM analytics.orders")
    blr = one(db, "SELECT sum(net_revenue) FROM analytics.orders WHERE city = 'Bengaluru'")
    assert 0 < blr < everywhere
    with acting_as(db, "assistant_ro", "Bengaluru"):
        # "What is our total revenue?" has no city in it; the answer must still be Bengaluru only
        assert one(db, "SELECT sum(net_revenue) FROM analytics.orders") == blr
        assert one(db, "SELECT count(DISTINCT city) FROM analytics.daily_city_sales") == 1
        assert one(db, "SELECT count(*) FROM analytics.orders WHERE city <> 'Bengaluru'") == 0
        # even asking for another city by name returns nothing
        assert one(db, "SELECT count(*) FROM analytics.orders WHERE city = 'Mumbai'") == 0


def test_joins_cannot_smuggle_other_cities_in(db):
    with acting_as(db, "assistant_ro", "Pune"):
        n = one(db, "SELECT count(*) FROM analytics.orders o JOIN analytics.order_items i USING"
                    " (order_id) JOIN analytics.restaurants r ON r.restaurant_id = o.restaurant_id"
                    " WHERE o.city <> 'Pune' OR i.city <> 'Pune' OR r.city <> 'Pune'")
        assert n == 0
        assert one(db, "SELECT count(*) FROM analytics.refunds r JOIN analytics.orders o USING"
                       " (order_id)") == one(db, "SELECT count(*) FROM analytics.refunds")


def test_aggregates_and_subqueries_are_scoped_too(db):
    with acting_as(db, "assistant_ro", "Chennai"):
        assert one(db, "SELECT count(*) FROM analytics.orders WHERE order_id IN"
                       " (SELECT order_id FROM analytics.order_items)") == one(
            db, "SELECT count(*) FROM analytics.orders")
        assert one(db, "SELECT count(DISTINCT restaurant_id) FROM analytics.orders") <= one(
            db, "SELECT count(*) FROM analytics.restaurants")


# --- limits of a request's scope --------------------------------------------------------------
def test_a_role_ceiling_cannot_be_widened_by_the_session(db):
    """People who connect straight to the database get their cities from ops.role_city_access,
    and cannot widen them with a SET. (The assistant's own ceiling is every city, which is why
    it also relies on the SQL guard and on set_config being unavailable.)"""
    db.execute("CREATE ROLE analyst_blr NOLOGIN IN ROLE analyst_city_role")
    db.execute("INSERT INTO ops.role_city_access VALUES ('analyst_blr', 'Bengaluru')")
    blr = one(db, "SELECT count(*) FROM analytics.orders WHERE city = 'Bengaluru'")
    for scope in ("ALL", "Bengaluru", "Bengaluru,Mumbai"):  # asking for more than the ceiling
        with acting_as(db, "analyst_blr", scope):
            assert one(db, "SELECT count(*) FROM analytics.orders") == blr, scope
    with acting_as(db, "analyst_blr", "Mumbai"):  # asking for a city outside the ceiling
        assert one(db, "SELECT count(*) FROM analytics.orders") == 0
    with acting_as(db, "analyst_blr"):  # no scope: fail closed
        assert one(db, "SELECT count(*) FROM analytics.orders") == 0


def test_a_restricted_role_with_no_ceiling_row_sees_nothing(db):
    db.execute("CREATE ROLE analyst_nobody NOLOGIN IN ROLE analyst_city_role")
    with acting_as(db, "analyst_nobody", "ALL"):
        assert one(db, "SELECT count(*) FROM analytics.orders") == 0


# --- the pipeline is not affected -------------------------------------------------------------
def test_the_pipeline_role_still_sees_and_writes_everything(db):
    with acting_as(db, "etl_writer"):
        assert one(db, "SELECT count(*) FROM gold.fact_orders") == everything(db, "orders")
        assert one(db, "SELECT count(*) FROM gold.dim_restaurant") > 0
        db.execute("UPDATE gold.fact_orders SET platform = platform"
                   " WHERE order_id = (SELECT min(order_id) FROM gold.fact_orders)")
        n = db.execute("UPDATE gold.dim_restaurant SET rating = rating").rowcount
        assert n == one(db, "SELECT count(*) FROM gold.dim_restaurant")


def test_the_scope_setting_does_not_change_what_the_pipeline_sees(db):
    with acting_as(db, "etl_writer", "Bengaluru"):  # a stray setting on a pipeline session
        assert one(db, "SELECT count(*) FROM gold.fact_orders") == everything(db, "orders")


# --- personal data ----------------------------------------------------------------------------
def test_no_view_row_contains_any_real_customer_detail(db):
    """Take every real name, email, address and phone number from the restricted table and look
    for each of them in every row of every view the assistant can read (the views return the same
    rows to any session that sees all cities)."""
    assert one(db, "SELECT count(*) FROM gold.customer_pii") > 100
    for view in allowed_views():  # run as admin: it can read the PII table to compare against
        found = one(db, f"SELECT count(*) FROM analytics.{view} t WHERE EXISTS (SELECT 1 FROM"
                        " gold.customer_pii p WHERE strpos(t::text, p.phone) > 0"
                        " OR strpos(t::text, p.email) > 0"
                        " OR strpos(t::text, p.address_line) > 0"
                        " OR strpos(t::text, p.name) > 0)")
        assert found == 0, view


def test_the_masked_phone_is_the_only_trace_of_a_phone(db):
    with acting_as(db, "assistant_ro", "ALL"):
        values = [v for (v,) in rows(db, "SELECT masked_phone FROM analytics.customers")]
    assert values and all(v.startswith("XXXXXX") and v[6:].isdigit() and len(v) == 10
                          for v in values)
