import pytest

from swiggy_ai.config import Settings
from swiggy_ai.db import connect
from swiggy_ai.logging_setup import scrub


def test_settings_defaults_and_optional_prices():
    s = Settings(_env_file=None, price_input_per_1m=None)
    assert s.max_heal_attempts == 3
    assert s.demo_fault_injection is False  # must be off by default
    assert s.price_input_per_1m is None


def test_scrub_removes_pii_and_keys():
    out = scrub("call 9876543210 or a.b@x.com key sk-abcdef123456789")
    assert "9876543210" not in out and "a.b@x.com" not in out and "sk-abcdef" not in out


@pytest.mark.db
def test_schemas_and_roles_exist():
    with connect("admin") as conn:
        rows = conn.execute("select schema_name from information_schema.schemata")
        schemas = {r[0] for r in rows}
        roles = {r[0] for r in conn.execute("select rolname from pg_roles")}
    assert {"bronze", "silver", "gold", "analytics", "semantic", "ops"} <= schemas
    assert {"etl_writer", "assistant_ro"} <= roles


@pytest.mark.db
def test_assistant_search_path_is_analytics():
    with connect("assistant") as conn:
        assert conn.execute("show search_path").fetchone()[0] == "analytics"


@pytest.mark.live
def test_llm_roundtrip():
    from swiggy_ai.check import check_llm
    ok, msg = check_llm()
    assert ok, msg


def test_model_tiers_fall_back_to_the_default_model(monkeypatch):
    from swiggy_ai import llm

    base = Settings(_env_file=None, openai_model="base", openai_model_low="cheap")
    monkeypatch.setattr(llm, "get_settings", lambda: base)
    assert llm.resolve_model("low") == "cheap"  # tier set
    assert llm.resolve_model("medium") == "base"  # tier empty -> default
    assert llm.resolve_model("high") == "base"

    empty = Settings(_env_file=None)
    monkeypatch.setattr(llm, "get_settings", lambda: empty)
    with pytest.raises(llm.LLMError, match="OPENAI_MODEL"):
        llm.resolve_model("low")
