"""`make refresh-catalog`: bring the machine-readable part of catalog.yml back in line with the
database, and say which columns still need a human description.

What is machine-readable (and so rewritten here): which columns each view has, and their types.
What is human knowledge (and so never touched): descriptions, examples, allowed values, synonyms,
join hints, do_not rules, metrics and the glossary.

The file is edited line by line, not regenerated, so comments and hand formatting survive.
  - a column that disappeared while one of the same type appeared  -> renamed: description kept
  - a new column                                                    -> added, flagged TODO
  - a column that disappeared with no replacement                   -> removed
  - a changed type                                                  -> updated
New or removed VIEWS are reported but not edited: a view needs a kind, a grain and rules that only
a person can write.
"""
import re
from dataclasses import dataclass, field
from pathlib import Path

import psycopg
import yaml

from swiggy_ai.semantic.check import database_columns
from swiggy_ai.semantic.loader import SEMANTIC_DIR, SemanticError, load_semantic

TODO = "TODO: describe this column."


@dataclass
class Plan:
    renamed: list[tuple[str, str, str]] = field(default_factory=list)  # (view, old, new)
    added: list[tuple[str, str]] = field(default_factory=list)  # (view, column)
    removed: list[tuple[str, str]] = field(default_factory=list)
    retyped: list[tuple[str, str, str, str]] = field(default_factory=list)  # view,col,old,new
    new_views: list[str] = field(default_factory=list)
    gone_views: list[str] = field(default_factory=list)
    needs_description: list[str] = field(default_factory=list)  # "view.column"
    metric_mentions: list[str] = field(default_factory=list)

    @property
    def changed(self) -> bool:
        return bool(self.renamed or self.added or self.removed or self.retyped)

    def lines(self) -> list[str]:
        out = [f"renamed  {v}.{o} -> {n} (description kept)" for v, o, n in self.renamed]
        out += [f"added    {v}.{c} (needs a description)" for v, c in self.added]
        out += [f"removed  {v}.{c}" for v, c in self.removed]
        out += [f"retyped  {v}.{c}: {a} -> {b}" for v, c, a, b in self.retyped]
        out += [f"NEW VIEW {v}: add it to catalog.yml by hand (kind, grain, rules)"
                for v in self.new_views]
        out += [f"GONE VIEW {v}: remove it from catalog.yml and check metrics.yml"
                for v in self.gone_views]
        out += [f"metrics.yml still mentions '{m}'" for m in self.metric_mentions]
        return out


def _column_nodes(text: str) -> dict[str, dict[str, tuple[int, int]]]:
    """view -> column -> (first line, last line), 0-based, found from the YAML parse itself."""
    root = yaml.compose(text)
    out: dict[str, dict[str, tuple[int, int]]] = {}
    tables = next(v for k, v in root.value if k.value == "tables")
    for table in tables.value:
        fields = {k.value: v for k, v in table.value}
        view = fields["name"].value
        out[view] = {}
        for col in fields["columns"].value:
            name = next(v.value for k, v in col.value if k.value == "name")
            last = col.end_mark.line - (1 if col.end_mark.column == 0 else 0)
            out[view][name] = (col.start_mark.line, last)
    return out


def plan_refresh(conn: psycopg.Connection, catalog_path: Path, schema: str = "analytics",
                 metrics_path: Path | None = None) -> tuple[Plan, list[str]]:
    """Work out what has to change. Returns the plan and the edited lines (not yet written)."""
    text = catalog_path.read_text()
    lines = text.split("\n")
    nodes = _column_nodes(text)
    doc = yaml.safe_load(text)
    documented = {t["name"]: {c["name"]: c for c in t["columns"]} for t in doc["tables"]}
    db = database_columns(conn, schema)
    comments = _column_comments(conn, schema)
    plan = Plan()
    plan.new_views = sorted(set(db) - set(documented))
    plan.gone_views = sorted(set(documented) - set(db))

    edits: list[tuple[int, int, list[str]]] = []  # (first line, last line, replacement lines)
    for view in sorted(set(db) & set(documented)):
        have, want = documented[view], db[view]
        gone = [c for c in have if c not in want]
        new = [c for c in want if c not in have]
        for old in list(gone):  # a rename: one disappeared, one appeared, same type
            twin = next((n for n in new if want[n] == have[old]["type"]), None)
            if twin and sum(1 for n in new if want[n] == have[old]["type"]) == 1 and sum(
                    1 for g in gone if have[g]["type"] == have[old]["type"]) == 1:
                first, last = nodes[view][old]
                edits.append((first, last, [_rename_line(lines[first], old, twin)]
                              + lines[first + 1:last + 1]))
                plan.renamed.append((view, old, twin))
                gone.remove(old)
                new.remove(twin)
        for old in gone:
            first, last = nodes[view][old]
            edits.append((first, last, []))
            plan.removed.append((view, old))
        for col in new:
            last_col = max(nodes[view].values(), key=lambda r: r[1])
            indent = re.match(r"\s*", lines[nodes[view][next(iter(nodes[view]))][0]]).group()
            desc = comments.get((view, col)) or TODO
            edits.append((last_col[1] + 1, last_col[1],
                          [f'{indent}- {{name: {col}, type: {want[col]}, '
                           f'description: "{desc}"}}']))
            plan.added.append((view, col))
        for col in sorted(set(have) & set(want)):
            if have[col]["type"] != want[col]:
                first, last = nodes[view][col]
                edits.append((first, last, [_retype_line(lines[i], want[col])
                                            if i == _type_line(lines, first, last) else lines[i]
                                            for i in range(first, last + 1)]))
                plan.retyped.append((view, col, have[col]["type"], want[col]))

    for first, last, replacement in sorted(edits, key=lambda e: (e[0], e[1]), reverse=True):
        lines[first:last + 1] = replacement  # bottom first, so earlier line numbers stay valid
    new_text = "\n".join(lines)

    after = yaml.safe_load(new_text)
    plan.needs_description = [f"{t['name']}.{c['name']}" for t in after["tables"]
                              for c in t["columns"] if str(c["description"]).startswith("TODO")]
    names = {c for _, c, *_ in plan.removed} | {o for _, o, _ in plan.renamed}
    mpath = metrics_path or catalog_path.with_name("metrics.yml")
    if mpath.exists():
        body = mpath.read_text()
        plan.metric_mentions = sorted(n for n in names if re.search(rf"\b{re.escape(n)}\b", body))
    return plan, new_text.split("\n")


def _type_line(lines: list[str], first: int, last: int) -> int:
    return next(i for i in range(first, last + 1) if re.search(r"\btype:\s*\w+", lines[i]))


def _rename_line(line: str, old: str, new: str) -> str:
    return re.sub(rf"(\bname:\s*){re.escape(old)}(?=[\s,}}]|$)", rf"\g<1>{new}", line, count=1)


def _retype_line(line: str, new: str) -> str:
    return re.sub(r"(\btype:\s*)\w+", rf"\g<1>{new}", line, count=1)


def _column_comments(conn: psycopg.Connection, schema: str) -> dict[tuple[str, str], str]:
    """A COMMENT ON COLUMN in the database, when someone wrote one, is a better start than TODO."""
    rows = conn.execute(
        "SELECT c.relname, a.attname, col_description(c.oid, a.attnum) FROM pg_class c"
        " JOIN pg_namespace n ON n.oid = c.relnamespace JOIN pg_attribute a ON a.attrelid = c.oid"
        " WHERE n.nspname = %s AND a.attnum > 0 AND NOT a.attisdropped", (schema,)).fetchall()
    return {(f"{schema}.{t}", c): d.replace('"', "'") for t, c, d in rows if d and len(d) >= 10}


def refresh_catalog(conn: psycopg.Connection, directory: Path | None = None,
                    write: bool = True) -> Plan:
    """Update catalog.yml (in `directory`, default semantic/). Refuses to leave a broken file."""
    directory = directory or SEMANTIC_DIR
    path = directory / "catalog.yml"
    original = path.read_text()
    plan, new_lines = plan_refresh(conn, path)
    if write and plan.changed:
        path.write_text("\n".join(new_lines))
        try:
            load_semantic(directory, check=False)
        except SemanticError:
            path.write_text(original)  # never leave the catalog unreadable
            raise
    return plan
