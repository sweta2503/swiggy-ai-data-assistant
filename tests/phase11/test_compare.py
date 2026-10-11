"""Phase 11, part 1: deciding whether a file's columns are the expected ones (no database)."""
import pytest
from pydantic import ValidationError

from swiggy_ai.pipeline.schema_drift import (
    ALIASES_PATH,
    Aliases,
    compare,
    fingerprint,
    load_aliases,
    suggest_renames,
)

EXPECTED = ["order_id", "order_value", "discount"]
NONE = Aliases()


def test_a_fingerprint_ignores_column_order_but_not_names():
    assert fingerprint(["a", "b", "c"]) == fingerprint(["c", "a", "b"])
    assert fingerprint(["a", "b"]) != fingerprint(["a", "c"])
    assert fingerprint(["a", "b"]) != fingerprint(["a", "b", "c"])
    assert len(fingerprint(["a"])) == 64


def test_the_same_columns_in_any_order_match():
    d = compare("orders", EXPECTED, ["discount", "order_id", "order_value"], NONE)
    assert d.status == "match"


def test_a_renamed_and_a_new_column_are_drift_without_approval():
    d = compare("orders", EXPECTED, ["order_id", "order_amount", "discount", "tip_amount"], NONE)
    assert d.status == "drift"
    assert d.missing == ["order_value"] and d.new == ["order_amount", "tip_amount"]
    assert d.suggested == {"order_amount": "order_value"}  # a hint only
    assert "missing" in d.describe() and "new" in d.describe()


def test_a_dropped_column_is_drift_too():
    d = compare("orders", EXPECTED, ["order_id", "order_value"], NONE)
    assert d.status == "drift" and d.missing == ["discount"] and d.new == []


def test_an_approved_rename_alone_is_not_enough_if_another_column_is_unexplained():
    a = Aliases(renames={"orders": {"order_amount": "order_value"}})
    d = compare("orders", EXPECTED, ["order_id", "order_amount", "discount", "tip_amount"], a)
    assert d.status == "drift" and d.new == ["tip_amount"] and d.missing == []
    assert d.renames == {"order_amount": "order_value"}


def test_rename_and_ignore_together_approve_the_file():
    a = Aliases(renames={"orders": {"order_amount": "order_value"}},
                ignore_new_columns={"orders": ["tip_amount"]})
    d = compare("orders", EXPECTED, ["order_id", "order_amount", "discount", "tip_amount"], a)
    assert d.status == "approved"
    assert d.renames == {"order_amount": "order_value"} and d.ignored == ["tip_amount"]


def test_approvals_are_per_file_type():
    a = Aliases(renames={"payments": {"order_amount": "order_value"}})
    status = compare("orders", EXPECTED, ["order_id", "order_amount", "discount"], a).status
    assert status == "drift"


def test_a_rename_is_ignored_when_it_makes_no_sense():
    # the new name is not something we expect
    a = Aliases(renames={"orders": {"order_amount": "price"}})
    status = compare("orders", EXPECTED, ["order_id", "order_amount", "discount"], a).status
    assert status == "drift"
    # the expected name is already in the file, so the rename would collide
    a = Aliases(renames={"orders": {"order_amount": "order_value"}})
    both = compare("orders", EXPECTED, ["order_id", "order_value", "order_amount", "discount"], a)
    assert both.status == "drift" and both.new == ["order_amount"]
    # the old name is not in the file at all: a stale approval does nothing
    stale = Aliases(renames={"orders": {"gone": "order_value"}})
    assert compare("orders", EXPECTED, ["order_id", "discount"], stale).status == "drift"


def test_ignoring_a_column_never_hides_a_missing_one():
    a = Aliases(ignore_new_columns={"orders": ["tip_amount"]})
    d = compare("orders", EXPECTED, ["order_id", "discount", "tip_amount"], a)
    assert d.status == "drift" and d.missing == ["order_value"]


def test_suggestions_pair_similar_names_and_make_no_wild_guesses():
    assert suggest_renames(["order_value"], ["order_amount"]) == {"order_amount": "order_value"}
    # one column gone and one appeared: a likely rename even when the names look nothing alike
    assert suggest_renames(["delivery_fee"], ["xyz"]) == {"xyz": "delivery_fee"}
    assert suggest_renames(["a", "b"], ["zzz"]) == {}
    assert suggest_renames([], ["tip_amount"]) == {}


def test_the_alias_file_rejects_typos_and_the_shipped_one_approves_nothing():
    with pytest.raises(ValidationError):
        Aliases(rename={"orders": {}})  # 'rename' is not 'renames'
    shipped = load_aliases()
    assert ALIASES_PATH.exists() and shipped.renames == {} and shipped.ignore_new_columns == {}
