import sqlglot
from sqlglot import exp

ALLOWED = {"orders", "order_items", "refunds", "customers",
           "restaurants", "calendar", "daily_city_sales", "data_as_of"}


def check(sql: str) -> tuple[bool, str]:
    """(True, safe_sql) or (False, reason)."""
    try:
        statements = sqlglot.parse(sql, read="postgres")
    except sqlglot.errors.SqlglotError as e:
        return False, f"cannot parse: {e}"
    if len(statements) != 1:
        return False, "exactly one statement is allowed"
    tree = statements[0]
    if not isinstance(tree, exp.Select):
        return False, f"only SELECT is allowed, not {type(tree).__name__}"
    ctes = {c.alias for c in tree.find_all(exp.CTE)}
    for table in tree.find_all(exp.Table):
        if table.name in ctes:
            continue
        if table.db not in ("", "analytics") or table.name not in ALLOWED:
            return False, f"table {table.db}.{table.name} is not allowed"
    return True, tree.limit(500).sql(dialect="postgres")
