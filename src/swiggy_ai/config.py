"""All settings in one typed place, loaded from .env."""
from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore", env_ignore_empty=True)

    openai_api_key: str = ""
    openai_model: str = ""  # never hard-coded: you choose the model in .env

    database_url_admin: str = "postgresql://swiggy_admin:admin_dev_pw@localhost:5433/swiggy"
    database_url_etl: str = "postgresql://etl_writer:etl_dev_pw@localhost:5433/swiggy"
    database_url_assistant: str = "postgresql://assistant_ro:assistant_dev_pw@localhost:5433/swiggy"

    api_key: str = ""

    max_heal_attempts: int = Field(default=3, ge=0, le=10)
    query_timeout_ms: int = Field(default=8000, ge=100)
    max_rows: int = Field(default=500, ge=1)
    max_plan_cost: float = 100_000

    demo_fault_injection: bool = False

    # Empty string in .env means "not set", so these stay optional.
    price_input_per_1m: float | None = None
    price_output_per_1m: float | None = None


@lru_cache
def get_settings() -> Settings:
    return Settings()
