"""Phase 9, part 1: what a question cost, and that personal data never reaches a record."""
import json
import logging
from decimal import Decimal

import pytest

from swiggy_ai.agents.state import Usage
from swiggy_ai.config import Settings
from swiggy_ai.logging_setup import JsonFormatter, scrub
from swiggy_ai.ops.audit import MAX_TEXT, clean
from swiggy_ai.ops.cost import NOT_COMPUTED, estimate_cost


def settings(**prices):
    return Settings(_env_file=None, **prices)


# --- usage ------------------------------------------------------------------------------------
def test_usage_is_counted_per_step_and_per_tier():
    u = Usage().plus("router", 100, 10, "low").plus("sql_writer", 2000, 150, "medium")
    u = u.plus("narrator", 300, 90, "low")
    assert (u.calls, u.tokens_in, u.tokens_out) == (3, 2400, 250)
    assert u.by_step["router"] == {"calls": 1, "tokens_in": 100, "tokens_out": 10}
    assert u.by_tier["low"] == {"calls": 2, "tokens_in": 400, "tokens_out": 100}
    assert u.by_tier["medium"]["tokens_in"] == 2000


def test_usage_is_immutable_so_a_node_cannot_corrupt_an_earlier_state():
    a = Usage().plus("router", 100, 10, "low")
    b = a.plus("router", 50, 5, "low")
    assert a.tokens_in == 100 and b.tokens_in == 150 and a.by_step["router"]["calls"] == 1


# --- cost -------------------------------------------------------------------------------------
def test_each_tier_is_priced_with_its_own_rates():
    u = Usage().plus("router", 1000, 100, "low").plus("sql_writer", 2000, 200, "medium")
    s = settings(price_input_per_1m_low=0.10, price_output_per_1m_low=0.40,
                 price_input_per_1m_medium=0.50, price_output_per_1m_medium=2.00)
    # low: (1000*0.10 + 100*0.40)/1e6 = 0.00014 ; medium: (2000*0.50 + 200*2.00)/1e6 = 0.0014
    assert estimate_cost(u, s).usd == Decimal("0.001540")


def test_the_plain_price_pair_applies_to_any_tier_without_its_own():
    u = Usage().plus("router", 1_000_000, 0, "low").plus("sql_writer", 0, 1_000_000, "medium")
    s = settings(price_input_per_1m=0.25, price_output_per_1m=1.50)
    assert estimate_cost(u, s).usd == Decimal("1.750000")


def test_a_tier_price_overrides_the_plain_pair():
    u = Usage().plus("router", 1_000_000, 0, "low")
    s = settings(price_input_per_1m=1.0, price_output_per_1m=1.0,
                 price_input_per_1m_low=0.1, price_output_per_1m_low=0.1)
    assert estimate_cost(u, s).usd == Decimal("0.100000")


def test_missing_prices_mean_not_computed_never_a_guess():
    u = Usage().plus("router", 1000, 100, "low")
    est = estimate_cost(u, settings())
    assert est.usd is None and not est.known and str(est) == NOT_COMPUTED
    assert NOT_COMPUTED == "cost not computed (prices not set)"


def test_half_a_price_is_not_a_price():
    u = Usage().plus("router", 1000, 100, "low")
    assert estimate_cost(u, settings(price_input_per_1m=0.1)).usd is None
    assert estimate_cost(u, settings(price_output_per_1m_low=0.1)).usd is None


def test_one_unpriced_tier_makes_the_whole_cost_unknown_and_says_which():
    u = Usage().plus("router", 1000, 100, "low").plus("sql_writer", 1000, 100, "medium")
    est = estimate_cost(u, settings(price_input_per_1m_low=0.1, price_output_per_1m_low=0.1))
    assert est.usd is None and "'medium'" in est.note


def test_a_question_that_called_no_model_cost_nothing_even_without_prices():
    assert estimate_cost(Usage(), settings()).usd == Decimal("0")  # e.g. a refusal


def test_cost_is_shown_in_dollars_to_six_places():
    u = Usage().plus("router", 1, 1, "low")
    est = estimate_cost(u, settings(price_input_per_1m=1.0, price_output_per_1m=1.0))
    assert str(est) == "$0.000002"


def test_empty_prices_in_the_environment_are_read_as_unset(monkeypatch):
    monkeypatch.setenv("PRICE_INPUT_PER_1M", "")
    assert Settings(_env_file=None).price_input_per_1m is None


# --- scrubbing --------------------------------------------------------------------------------
@pytest.mark.parametrize("text, secret", [
    ("call me on 9876543210 please", "9876543210"),
    ("my number is +91 98765 43210", "98765 43210"),
    ("whatsapp 91-9876543210", "9876543210"),
    ("phone 98765-43210", "98765-43210"),
    ("mail priya.sharma+test@example.co.in about it", "priya.sharma"),
    ("key sk-proj-AbCd1234_efGh5678-ijKl9012", "AbCd1234"),
    ("Authorization: Bearer abcdefghijklmnopqrstuvwxyz0123456789", "abcdefghijklmnop"),
    ("card 4111 1111 1111 1111 expires soon", "4111 1111 1111 1111"),
    ("card 4111-1111-1111-1111", "4111-1111-1111-1111"),
    ("aadhaar 1234 5678 9012", "1234 5678 9012"),
    ("pan ABCDE1234F", "ABCDE1234F"),
])
def test_personal_data_and_secrets_are_removed(text, secret):
    out = scrub(text)
    assert secret not in out
    assert "[" in out  # replaced by a marker, not silently dropped


@pytest.mark.parametrize("text", [
    "How many orders in Pune last week?",
    "ORD00000001 was delivered on 2026-03-01 13:05:44",
    "CUST000001 spent 63748004.83 rupees",
    "Top 5 restaurants by net revenue",
    "SELECT SUM(order_value) FROM analytics.orders WHERE order_value > 5000 LIMIT 500",
    "refund_rate was 0.0402 and AOV was 425.5",
    "ORD90000001 and REST00042 and ITEM0000123",
    "between 2025-10-09 and 2026-10-08",
])
def test_ordinary_business_text_is_left_alone(text):
    assert scrub(text) == text


def test_scrubbing_is_idempotent_and_handles_several_items_at_once():
    text = "a@b.com 9876543210 sk-abcdefgh12345678 4111111111111111"
    once = scrub(text)
    assert scrub(once) == once and once.count("[") == 4


def test_log_lines_are_scrubbed_before_they_are_written():
    rec = logging.LogRecord("t", logging.ERROR, __file__, 1,
                            "failed for priya@example.com on 9876543210 with sk-proj-abc12345678",
                            None, None)
    line = json.loads(JsonFormatter().format(rec))["msg"]
    assert "priya" not in line and "9876543210" not in line and "abc12345678" not in line


def test_log_extra_fields_are_scrubbed_too():
    rec = logging.LogRecord("t", logging.INFO, __file__, 1, "x", None, None)
    rec.fields = {"user": "priya@example.com"}
    assert "priya" not in JsonFormatter().format(rec)


def test_stored_text_is_scrubbed_and_length_limited():
    assert clean("ask 9876543210") == "ask [phone]"
    assert clean(None) is None
    long = clean("x" * 5000)
    assert len(long) == MAX_TEXT and long.endswith("…")
    assert len(clean("y" * 5000, 4000)) == 4000
