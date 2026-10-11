"""The SQL guard: decides whether a piece of generated SQL may reach the database.

This is plain code, not a prompt and not a model. The model proposes; the guard disposes.
It is one of five layers (see notes/chapter_07.md); the database enforces the same rules again.

A query passes only if ALL of these hold:
  * it parses, and is exactly ONE statement
  * the statement is a SELECT (a WITH ... SELECT, or a UNION of SELECTs) and contains nothing that
    changes data or state anywhere inside it (no data-modifying CTEs, no SELECT INTO, no FOR UPDATE)
  * every table it reads is an allow-listed analytics view (or a CTE it defined itself)
  * it calls no function on the deny list (sleep, file access, settings, dblink, large objects ...)
  * it contains no SQL comments, which are the usual way to hide a second statement
Accepted queries are rewritten so a LIMIT of at most MAX_ROWS is always present.
"""
import logging
import re
from dataclasses import dataclass

import sqlglot
from sqlglot import exp

logging.getLogger("sqlglot").setLevel(logging.ERROR)  # it warns about every non-SELECT we reject

MAX_SQL_CHARS = 8000

# Reasons a query can be refused. The healer is never given any of these: a refusal is a boundary,
# not a bug to work around.
CODES = ("empty", "too_long", "parse_error", "comment", "multiple_statements", "not_select",
         "forbidden_statement", "forbidden_relation", "forbidden_function", "into_clause",
         "lock_clause")

# Anything that is not a plain read. Looked for ANYWHERE in the tree, so it also catches a
# data-modifying CTE:  WITH d AS (DELETE FROM t RETURNING *) SELECT * FROM d
FORBIDDEN_NODES = tuple(
    getattr(exp, name) for name in (
        "Insert", "Update", "Delete", "Merge", "Create", "Drop", "Alter", "TruncateTable",
        "Copy", "Command", "Set", "SetItem", "Use", "Transaction", "Commit", "Rollback", "Grant",
        "Revoke", "Pragma", "Analyze", "Describe", "Show", "Declare", "Execute", "Kill",
        "LoadData", "Refresh", "Comment",
    ) if hasattr(exp, name))

# Functions that sleep, read files, run text as SQL, read or change settings, or reveal the server.
DENY_EXACT = {
    "set_config", "current_setting", "version", "current_version", "nextval", "currval",
    "setval", "lastval", "dblink", "copy", "inet_server_addr", "inet_server_port",
    "to_regclass", "to_regproc", "to_regtype", "format_type", "current_database",
    "current_schema", "current_schemas", "current_user", "session_user", "current_role", "user",
}
DENY_PREFIXES = ("pg_", "lo_", "dblink", "query_to_", "cursor_to_", "table_to_", "schema_to_",
                 "database_to_", "xpath", "ts_debug", "txid_", "has_")
# Set-returning functions that may appear in FROM (everything else in FROM must be a view or CTE)
ALLOWED_TABLE_FUNCTIONS = {"generate_series", "unnest", "explodinggenerateseries"}


@dataclass(frozen=True)
class GuardResult:
    ok: bool
    sql: str  # the SQL to run (rewritten with a LIMIT) when ok, else the original text
    code: str = ""  # one of CODES when not ok
    reason: str = ""  # a sentence a human can read
    changes: tuple[str, ...] = ()  # what the guard altered, e.g. ("added LIMIT 500",)


def _refuse(sql: str, code: str, reason: str) -> GuardResult:
    return GuardResult(False, sql, code, reason)


def _function_name(f: exp.Func) -> str:
    name = f.name if isinstance(f, exp.Anonymous) else f.sql_name()
    return (name or "").strip('"').lower()


def _denied_function(name: str) -> bool:
    if not name:
        return False
    return name in DENY_EXACT or name.startswith(DENY_PREFIXES)


def check_sql(sql: str, allowed_views: set[str], max_rows: int = 500) -> GuardResult:
    """Judge one piece of SQL. `allowed_views` holds bare view names (e.g. {'orders', ...})."""
    text = (sql or "").strip()
    if not text.strip("; \n\t"):
        return _refuse(sql, "empty", "The query is empty.")
    if len(text) > MAX_SQL_CHARS:
        return _refuse(sql, "too_long", f"The query is longer than {MAX_SQL_CHARS} characters.")

    try:
        tokens = sqlglot.tokenize(text, read="postgres")
        statements = [s for s in sqlglot.parse(text, read="postgres") if s is not None]
    except sqlglot.errors.SqlglotError as e:
        return _refuse(sql, "parse_error", f"The query could not be parsed: {str(e)[:120]}")

    if any(t.comments for t in tokens):
        return _refuse(sql, "comment", "SQL comments are not allowed.")
    if len(statements) != 1:
        return _refuse(sql, "multiple_statements",
                       f"Exactly one statement is allowed, found {len(statements)}.")
    tree = statements[0]
    while isinstance(tree, exp.Subquery) and not tree.args.get("alias"):
        tree = tree.this  # "(SELECT ...)" is still just a SELECT

    if not isinstance(tree, (exp.Select, exp.SetOperation)):
        return _refuse(sql, "not_select",
                       f"Only SELECT queries are allowed (this is a {type(tree).__name__}).")

    for node in tree.walk():
        if isinstance(node, FORBIDDEN_NODES):
            return _refuse(sql, "forbidden_statement",
                           f"A {type(node).__name__} operation is not allowed inside a query.")
        if isinstance(node, exp.Select):
            if node.args.get("into"):
                return _refuse(sql, "into_clause", "SELECT ... INTO creates a table.")
            if node.args.get("locks"):
                return _refuse(sql, "lock_clause", "Row locks (FOR UPDATE) are not allowed.")

    ctes = {c.alias.lower() for c in tree.find_all(exp.CTE) if c.alias}
    for t in tree.find_all(exp.Table):
        if isinstance(t.this, exp.Func) or not t.name:  # a table function such as generate_series
            kind = type(t.this).__name__
            fn = _function_name(t.this) if isinstance(t.this, exp.Func) else kind
            if fn not in ALLOWED_TABLE_FUNCTIONS and kind.lower() not in ALLOWED_TABLE_FUNCTIONS:
                return _refuse(sql, "forbidden_relation",
                               f"The table function '{fn or kind}' is not allowed.")
            continue
        name, schema, catalog = t.name.lower(), (t.db or "").lower(), (t.catalog or "").lower()
        if catalog:
            return _refuse(sql, "forbidden_relation",
                           f"'{catalog}.{schema}.{name}' is not allowed.")
        if schema == "" and name in ctes:
            continue
        if schema in ("", "analytics") and name in allowed_views:
            continue
        shown = f"{schema}.{name}" if schema else name
        return _refuse(sql, "forbidden_relation",
                       f"'{shown}' is not one of the allowed analytics views.")

    for f in tree.find_all(exp.Func):
        name = _function_name(f)
        if _denied_function(name):
            return _refuse(sql, "forbidden_function", f"The function '{name}' is not allowed.")

    rewritten, changes = _enforce_limit(tree, max_rows)
    if _nulls_last_for_descending(rewritten, text):
        changes.append("DESC sorts put NULLs last")
    return GuardResult(True, rewritten.sql(dialect="postgres"), changes=tuple(changes))


def _nulls_last_for_descending(tree: exp.Expression, original: str) -> bool:
    """Postgres puts NULLs FIRST in a descending sort, so "top 5 by revenue" would start with the
    rows that have no revenue. Say NULLS LAST unless the query asked for NULLS FIRST by name."""
    if re.search(r"\bnulls\s+first\b", original, re.IGNORECASE):
        return False
    changed = False
    for ordered in tree.find_all(exp.Ordered):
        if ordered.args.get("desc") and ordered.args.get("nulls_first"):
            ordered.set("nulls_first", False)
            changed = True
    return changed


def _enforce_limit(tree: exp.Expression, max_rows: int) -> tuple[exp.Expression, list[str]]:
    """Make sure the outermost query has a LIMIT no larger than max_rows."""
    limit = tree.args.get("limit")
    if limit is None:
        return tree.limit(max_rows), [f"added LIMIT {max_rows}"]
    expr = limit.args.get("expression")
    if isinstance(expr, exp.Literal) and expr.is_int and int(expr.name) <= max_rows:
        return tree, []
    return tree.limit(max_rows), [f"capped LIMIT at {max_rows}"]


def referenced_views(sql: str) -> set[str]:
    """Bare names of the views a (valid) query reads, for reporting."""
    tree = sqlglot.parse_one(sql, read="postgres")
    ctes = {c.alias.lower() for c in tree.find_all(exp.CTE) if c.alias}
    names = {t.name.lower() for t in tree.find_all(exp.Table) if t.name}
    return names - ctes
