"""All settings in one typed place, loaded from .env."""
from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore", env_ignore_empty=True)

    openai_api_key: str = ""
    openai_model: str = ""  # default model, never hard-coded: you choose it in .env
    # Optional per-job models. Empty means "use OPENAI_MODEL". Typical split:
    #   low    = rewriting a question, narrating an answer (cheap)
    #   medium = writing and repairing SQL (needs to be right)
    #   high   = hard cases only
    openai_model_low: str = ""
    openai_model_medium: str = ""
    openai_model_high: str = ""

    database_url_admin: str = "postgresql://swiggy_admin:admin_dev_pw@localhost:5433/swiggy"
    database_url_etl: str = "postgresql://etl_writer:etl_dev_pw@localhost:5433/swiggy"
    database_url_assistant: str = "postgresql://assistant_ro:assistant_dev_pw@localhost:5433/swiggy"

    api_key: str = ""  # the admin user's API key
    api_key_bengaluru: str = ""
    api_key_mumbai: str = ""

    max_heal_attempts: int = Field(default=3, ge=0, le=10)
    query_timeout_ms: int = Field(default=8000, ge=100)
    max_rows: int = Field(default=500, ge=1)
    max_plan_cost: float = 100_000

    answer_cache: bool = True  # the API and UI serve repeated first questions from ops.answer_cache
    demo_fault_injection: bool = False
    demo_fault_name: str = ""  # which entry of eval/fault_injection.yml to use (default: first)

    llm_sanity_check: bool = False  # optional extra model read of results (warnings only)
    context_tokens: int = Field(default=2200, ge=300)  # budget for the semantic context

    # Empty string in .env means "not set", so these stay optional.
    # US dollars per 1,000,000 tokens, from the OpenAI pricing page. The plain pair applies to every
    # model; the per-tier pairs (low / medium / high) override it, which matters when the tiers use
    # different models. Leave them all empty and costs are simply not computed.
    price_input_per_1m: float | None = None
    price_output_per_1m: float | None = None
    price_input_per_1m_low: float | None = None
    price_output_per_1m_low: float | None = None
    price_input_per_1m_medium: float | None = None
    price_output_per_1m_medium: float | None = None
    price_input_per_1m_high: float | None = None
    price_output_per_1m_high: float | None = None


@lru_cache
def get_settings() -> Settings:
    return Settings()
