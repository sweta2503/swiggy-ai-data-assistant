"""Everything the nodes need from the outside world, in one object.

Nodes never import the database or OpenAI directly; they use the Runtime. That keeps every node a
small function that is easy to test with a scripted fake model and a real (or fake) database.
"""
import re
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any, TypeVar

import psycopg
from pydantic import BaseModel

from swiggy_ai.agents.state import AgentState, Usage
from swiggy_ai.config import Settings, get_settings
from swiggy_ai.db import connect
from swiggy_ai.llm import LLMResult, Tier, complete_structured
from swiggy_ai.security.scope import assistant_connection
from swiggy_ai.semantic.loader import Semantic, load_semantic

T = TypeVar("T", bound=BaseModel)
PROMPTS_DIR = Path(__file__).parent / "prompts"
_HEADER = re.compile(r"^<!--.*?-->\s*", re.S)


def load_prompt(name: str) -> str:
    """Prompts are plain files, one per agent, so anyone can read what the model is told."""
    return _HEADER.sub("", (PROMPTS_DIR / f"{name}.md").read_text()).strip()


def jsonable(value: Any) -> Any:
    """Database values as plain JSON-friendly Python, so state can be logged and replayed."""
    if isinstance(value, Decimal):
        return int(value) if value == value.to_integral_value() and abs(value) < 10**15 \
            else float(value)
    if isinstance(value, datetime | date):
        return value.isoformat(sep=" ") if isinstance(value, datetime) else value.isoformat()
    if value is None or isinstance(value, bool | int | float | str):
        return value
    return str(value)


@dataclass
class Runtime:
    settings: Settings
    semantic: Semantic
    llm: Callable[..., LLMResult]  # same call shape as llm.complete_structured
    open_assistant: Callable[[str], psycopg.Connection]  # scope -> read-only scoped connection
    open_service: Callable[[], psycopg.Connection]  # the app's own connection (health, metadata)
    clock: Callable[[], float]
    # data-quality warnings to show beside answers; None means ask the database
    health_check: Callable[[], list[str]] | None = None

    @classmethod
    def create(cls, settings: Settings | None = None) -> "Runtime":
        import time
        return cls(settings=settings or get_settings(), semantic=load_semantic(),
                   llm=complete_structured,
                   open_assistant=assistant_connection,
                   open_service=lambda: connect("etl", autocommit=True), clock=time.time)

    def health_warnings(self) -> list[str]:
        """Data-quality warnings to show beside an answer (empty when the data is healthy)."""
        if self.health_check is not None:
            return self.health_check()
        from swiggy_ai.dq.runner import assert_gold_healthy
        with self.open_service() as conn:
            health = assert_gold_healthy(conn)
        return [] if health else [health.message]

    @property
    def allowed_views(self) -> set[str]:
        return {t.name.split(".", 1)[1] for t in self.semantic.catalog.tables}

    def ask(self, state: AgentState, step: str, prompt: str, user_input: str, schema: type[T],
            tier: Tier = "medium", temperature: float | None = 0.0) -> tuple[T, Usage]:
        """Call the model for one step. Returns the parsed answer and the updated usage."""
        r = self.llm(instructions=load_prompt(prompt), user_input=user_input, schema=schema,
                     tier=tier, temperature=temperature)
        return r.parsed, state.usage.plus(step, r.usage.input_tokens, r.usage.output_tokens)
