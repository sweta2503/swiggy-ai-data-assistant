"""Phase 7, part 3: API keys map to users and cities, and the key itself is never stored."""
import psycopg
import pytest

from swiggy_ai.security.scope import (
    ScopeError,
    hash_key,
    lookup_user,
    new_api_key,
    normalise_scope,
    seed_user,
)
from tests.phase04.helpers import make_raw
from tests.phase07.helpers import admin_connection_with_data

pytestmark = pytest.mark.db
KEY = "test-key-0123456789abcdef"


@pytest.fixture(scope="module")
def raw(tmp_path_factory):
    return make_raw(tmp_path_factory.mktemp("raw"))


@pytest.fixture(scope="module")
def db(raw):
    c = admin_connection_with_data(raw)
    yield c
    c.rollback()
    c.close()


@pytest.fixture
def users(db):
    with db.transaction():  # per-test savepoint, rolled back after
        db.execute("DELETE FROM ops.app_users")
        yield db
        raise psycopg.Rollback()


def test_an_api_key_finds_its_user_and_cities(users):
    seed_user(users, "u1", "Pune manager", KEY, "pune")
    u = lookup_user(users, KEY)
    assert (u.user_id, u.display_name, u.allowed_cities) == ("u1", "Pune manager", "Pune")
    assert not u.sees_everything


def test_the_key_is_stored_only_as_a_hash(users):
    seed_user(users, "u1", "Pune manager", KEY, "Pune")
    dump = users.execute("SELECT row_to_json(u)::text FROM ops.app_users u").fetchone()[0]
    assert KEY not in dump and hash_key(KEY) in dump
    assert len(hash_key(KEY)) == 64


def test_wrong_empty_or_missing_keys_find_nobody(users):
    seed_user(users, "u1", "Pune manager", KEY, "Pune")
    for bad in ("wrong-key-0123456789abcdef", "", None, KEY.upper(), KEY + " ", " " + KEY):
        assert lookup_user(users, bad) is None, repr(bad)


def test_a_deactivated_user_is_locked_out_at_once(users):
    seed_user(users, "u1", "Pune manager", KEY, "Pune")
    users.execute("UPDATE ops.app_users SET is_active = false WHERE user_id = 'u1'")
    assert lookup_user(users, KEY) is None


def test_changing_a_key_invalidates_the_old_one(users):
    seed_user(users, "u1", "Pune manager", KEY, "Pune")
    seed_user(users, "u1", "Pune manager", "another-key-0123456789ab", "Pune")
    assert lookup_user(users, KEY) is None
    assert lookup_user(users, "another-key-0123456789ab").user_id == "u1"


def test_two_users_cannot_share_a_key(users):
    seed_user(users, "u1", "A", KEY, "Pune")
    with pytest.raises(Exception, match="api_key_hash|duplicate key"):
        seed_user(users, "u2", "B", KEY, "Mumbai")


def test_short_keys_are_refused(users):
    with pytest.raises(ScopeError, match="shorter than 16"):
        seed_user(users, "u1", "A", "short", "Pune")


def test_cities_are_checked_against_the_real_city_list(users):
    assert normalise_scope(users, "all") == "ALL"
    assert normalise_scope(users, " pune ,  MUMBAI,pune") == "Mumbai,Pune"
    for bad in ("", "  ", "Atlantis", "Pune,Atlantis", "Pune;"):
        with pytest.raises(ScopeError):
            normalise_scope(users, bad)
    with pytest.raises(ScopeError):
        seed_user(users, "u1", "A", KEY, "Atlantis")


def test_all_means_everything(users):
    seed_user(users, "boss", "Admin", KEY, "all")
    u = lookup_user(users, KEY)
    assert u.allowed_cities == "ALL" and u.sees_everything


def test_generated_keys_are_long_and_unique():
    keys = {new_api_key() for _ in range(50)}
    assert len(keys) == 50 and all(len(k) >= 30 for k in keys)
