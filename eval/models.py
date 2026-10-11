"""The shape of eval/golden.yml. Typos in field names are errors, not silently ignored."""
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator

EVAL_DIR = Path(__file__).resolve().parent
Category = Literal["simple", "joins", "time", "multi_step", "ambiguous", "unsafe", "trap",
                   "security"]
DATA_TYPES = ("number", "table", "numbers_present", "contains")
MIN_PER_CATEGORY = {"simple": 6, "joins": 6, "time": 5, "multi_step": 4, "ambiguous": 2,
                    "unsafe": 2, "trap": 3, "security": 2}


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Expectation(Strict):
    type: Literal["number", "table", "numbers_present", "contains", "clarification", "refusal"]
    reference_sql: str | None = None
    ordered: bool = False
    accept_percent: bool = False
    min_match: int | None = None

    @model_validator(mode="after")
    def _check(self) -> "Expectation":
        needs_sql = self.type in DATA_TYPES
        if needs_sql and not self.reference_sql:
            raise ValueError(f"a '{self.type}' expectation needs a reference_sql")
        if not needs_sql and self.reference_sql:
            raise ValueError(f"a '{self.type}' expectation must not have a reference_sql")
        if self.reference_sql and not (EVAL_DIR / "reference_sql" / self.reference_sql).exists():
            raise ValueError(f"reference_sql file not found: {self.reference_sql}")
        return self


class Question(Strict):
    id: str = Field(pattern=r"^[A-Z]+\d+$")
    category: Category
    user: str
    question: str = Field(min_length=3)
    expected: list[Expectation] = Field(min_length=1)

    @property
    def answerable(self) -> bool:
        """Does a good assistant produce data for this question (vs. ask or refuse)?"""
        return all(e.type in DATA_TYPES for e in self.expected)


class Golden(Strict):
    users: dict[str, str]
    questions: list[Question]

    @model_validator(mode="after")
    def _check(self) -> "Golden":
        ids = [q.id for q in self.questions]
        dupes = {i for i in ids if ids.count(i) > 1}
        if dupes:
            raise ValueError(f"duplicate question ids {sorted(dupes)}")
        for q in self.questions:
            if q.user not in self.users:
                raise ValueError(f"{q.id}: unknown user '{q.user}'")
        return self


def load_golden(path: Path | None = None) -> Golden:
    return Golden.model_validate(yaml.safe_load((path or EVAL_DIR / "golden.yml").read_text()))
