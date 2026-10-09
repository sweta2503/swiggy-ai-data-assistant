"""Typed shapes of the semantic layer files. Unknown fields are errors, so typos are caught."""
import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

ColumnType = Literal["text", "integer", "numeric", "date", "timestamp", "boolean"]


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Column(Strict):
    name: str
    type: ColumnType
    description: str = Field(min_length=10)
    examples: list[str] = []
    allowed_values: list[str] = []  # the complete list of values this column can hold
    pii: bool = False  # True would mean personal data; analytics views may not have any
    masked: bool = False  # a deliberately masked form of personal data (e.g. last 4 digits)


class Table(Strict):
    name: str = Field(pattern=r"^analytics\.[a-z_]+$")
    kind: Literal["fact", "dimension", "aggregate", "reference"]
    description: str = Field(min_length=10)
    grain: str = Field(min_length=3)
    synonyms: list[str] = []
    join_hints: list[str] = []
    do_not: list[str] = []
    columns: list[Column] = Field(min_length=1)

    @model_validator(mode="after")
    def _unique_columns(self) -> "Table":
        names = [c.name for c in self.columns]
        dupes = {n for n in names if names.count(n) > 1}
        if dupes:
            raise ValueError(f"{self.name}: duplicate columns {sorted(dupes)}")
        return self

    def column(self, name: str) -> Column | None:
        return next((c for c in self.columns if c.name == name), None)


class Catalog(Strict):
    tables: list[Table] = Field(min_length=1)

    @model_validator(mode="after")
    def _unique_tables(self) -> "Catalog":
        names = [t.name for t in self.tables]
        dupes = {n for n in names if names.count(n) > 1}
        if dupes:
            raise ValueError(f"duplicate tables {sorted(dupes)}")
        return self

    def table(self, name: str) -> Table | None:
        return next((t for t in self.tables if t.name == name), None)


class Metric(Strict):
    name: str = Field(pattern=r"^[a-z][a-z0-9_]*$")
    description: str = Field(min_length=10)
    source: str
    time_column: str
    sql_expression: str = Field(min_length=3)
    grain: str
    filters: list[str] = []
    synonyms: list[str] = []
    owner: str = Field(min_length=2)
    unit: Literal["count", "rupees", "ratio", "minutes"]

    @field_validator("sql_expression")
    @classmethod
    def _single_line_no_semicolon(cls, v: str) -> str:
        if ";" in v:
            raise ValueError("a metric expression must be one expression, not a statement")
        return re.sub(r"\s+", " ", v.strip())


class Conventions(Strict):
    timezone: str
    week_starts_on: Literal["Monday", "Sunday"]
    reference_date: str
    rules: list[str] = []
    period_templates: dict[str, str] = {}


class MetricsFile(Strict):
    conventions: Conventions
    metrics: list[Metric] = Field(min_length=1)

    @model_validator(mode="after")
    def _unique_metrics(self) -> "MetricsFile":
        names = [m.name for m in self.metrics]
        dupes = {n for n in names if names.count(n) > 1}
        if dupes:
            raise ValueError(f"duplicate metrics {sorted(dupes)}")
        return self

    def metric(self, name: str) -> Metric | None:
        return next((m for m in self.metrics if m.name == name), None)


class GlossaryEntry(Strict):
    term: str
    also_called: list[str] = []
    definition: str


REQUIRED_METRICS = ["orders", "delivered_orders", "gross_order_value", "net_revenue", "aov",
                    "cancellation_rate", "avg_delivery_minutes", "refund_rate",
                    "active_customers", "new_customers"]
