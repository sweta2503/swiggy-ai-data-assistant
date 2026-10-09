"""Read the semantic files and check them against each other.

Loading answers "is each file well formed?". `validate()` answers "do they agree?": every metric
points at a real view and real columns, and nothing exposes personal data.
"""
from dataclasses import dataclass
from pathlib import Path

import sqlglot
import yaml
from pydantic import ValidationError
from sqlglot import exp

from swiggy_ai.semantic.models import (
    REQUIRED_METRICS,
    Catalog,
    GlossaryEntry,
    MetricsFile,
)

SEMANTIC_DIR = Path(__file__).resolve().parents[3] / "semantic"


class SemanticError(ValueError):
    """The semantic files are wrong. `problems` lists every issue found."""

    def __init__(self, problems: list[str]):
        super().__init__("\n".join(problems))
        self.problems = problems


@dataclass
class Semantic:
    catalog: Catalog
    metrics: MetricsFile
    glossary: list[GlossaryEntry]


def _read_yaml(path: Path) -> dict:
    try:
        data = yaml.safe_load(path.read_text())
    except yaml.YAMLError as e:
        raise SemanticError([f"{path.name}: not valid YAML: {e}"]) from e
    if not isinstance(data, dict):
        raise SemanticError([f"{path.name}: expected a mapping at the top level"])
    return data


def _validation_problems(path: Path, e: ValidationError) -> list[str]:
    return [f"{path.name}: {'.'.join(str(p) for p in err['loc'])}: {err['msg']}"
            for err in e.errors()]


def load_catalog(path: Path | None = None) -> Catalog:
    path = path or SEMANTIC_DIR / "catalog.yml"
    try:
        return Catalog.model_validate(_read_yaml(path))
    except ValidationError as e:
        raise SemanticError(_validation_problems(path, e)) from e


def load_metrics(path: Path | None = None) -> MetricsFile:
    path = path or SEMANTIC_DIR / "metrics.yml"
    try:
        return MetricsFile.model_validate(_read_yaml(path))
    except ValidationError as e:
        raise SemanticError(_validation_problems(path, e)) from e


def load_glossary(path: Path | None = None) -> list[GlossaryEntry]:
    """Each '## Term' heading starts an entry; an optional 'Also called:' line follows."""
    path = path or SEMANTIC_DIR / "glossary.md"
    entries: list[GlossaryEntry] = []
    term, lines = None, []

    def flush() -> None:
        if term is None:
            return
        also: list[str] = []
        body = []
        for line in lines:
            if line.lower().startswith("also called:"):
                also = [a.strip() for a in line.split(":", 1)[1].split(",") if a.strip()]
            elif line.strip():
                body.append(line.strip())
        entries.append(GlossaryEntry(term=term, also_called=also, definition=" ".join(body)))

    for line in path.read_text().splitlines():
        if line.startswith("## "):
            flush()
            term, lines = line[3:].strip(), []
        elif term is not None:
            lines.append(line)
    flush()
    return entries


def expression_columns(expression: str) -> set[str]:
    """Names of all columns an SQL expression reads (parsed, not guessed with a regex)."""
    tree = sqlglot.parse_one(expression, read="postgres")
    return {c.name for c in tree.find_all(exp.Column)}


def validate(catalog: Catalog, metrics: MetricsFile, glossary: list[GlossaryEntry]) -> list[str]:
    problems: list[str] = []

    for t in catalog.tables:
        for c in t.columns:
            if c.pii:
                problems.append(f"{t.name}.{c.name}: personal data (pii: true) may not be exposed")
            if c.allowed_values and len(set(c.allowed_values)) != len(c.allowed_values):
                problems.append(f"{t.name}.{c.name}: allowed_values has duplicates")

    for name in REQUIRED_METRICS:
        if metrics.metric(name) is None:
            problems.append(f"metrics.yml: required metric '{name}' is missing")

    for m in metrics.metrics:
        table = catalog.table(m.source)
        if table is None:
            problems.append(f"metric {m.name}: source '{m.source}' is not in the catalog")
            continue
        tc = table.column(m.time_column)
        if tc is None or tc.type not in ("date", "timestamp"):
            problems.append(f"metric {m.name}: time_column '{m.time_column}' is not a date "
                            f"column of {m.source}")
        try:
            used = expression_columns(m.sql_expression)
        except sqlglot.errors.ParseError as e:
            problems.append(f"metric {m.name}: sql_expression does not parse: {e}")
            continue
        for col in sorted(used):
            if table.column(col) is None:
                problems.append(f"metric {m.name}: uses column '{col}' which {m.source} "
                                "does not have")

    terms = [g.term.lower() for g in glossary]
    for dup in {t for t in terms if terms.count(t) > 1}:
        problems.append(f"glossary.md: term '{dup}' appears twice")
    for g in glossary:
        if len(g.definition) < 15:
            problems.append(f"glossary.md: '{g.term}' needs a definition")
    return problems


def load_semantic(directory: Path | None = None, check: bool = True) -> Semantic:
    d = directory or SEMANTIC_DIR
    sem = Semantic(load_catalog(d / "catalog.yml"), load_metrics(d / "metrics.yml"),
                   load_glossary(d / "glossary.md"))
    if check:
        problems = validate(sem.catalog, sem.metrics, sem.glossary)
        if problems:
            raise SemanticError(problems)
    return sem
