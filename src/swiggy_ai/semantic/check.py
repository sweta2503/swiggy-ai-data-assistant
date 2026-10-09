"""`make catalog-check`: the catalog must describe exactly what the database exposes."""
import re

import psycopg

from swiggy_ai.semantic.loader import Semantic

PG_TYPES = {
    "text": "text", "character varying": "text", "integer": "integer", "smallint": "integer",
    "bigint": "integer", "numeric": "numeric", "date": "date", "boolean": "boolean",
    "timestamp without time zone": "timestamp", "timestamp with time zone": "timestamp",
}
# Column names that look like personal data. Exposing one needs `masked: true` in the catalog.
PII_NAME = re.compile(r"(phone|e_?mail|address|pincode|aadhaar|passport|ssn|birth|"
                      r"(first|last|full|customer|user)_name|^name$)", re.IGNORECASE)


def database_columns(conn: psycopg.Connection, schema: str = "analytics"
                     ) -> dict[str, dict[str, str]]:
    rows = conn.execute(
        "SELECT table_name, column_name, data_type FROM information_schema.columns"
        " WHERE table_schema = %s ORDER BY table_name, ordinal_position", (schema,)).fetchall()
    out: dict[str, dict[str, str]] = {}
    for table, column, data_type in rows:
        out.setdefault(f"{schema}.{table}", {})[column] = PG_TYPES.get(data_type, data_type)
    return out


def check_catalog(conn: psycopg.Connection, sem: Semantic, schema: str = "analytics",
                  check_values: bool = True) -> list[str]:
    """Return a list of problems (empty = the catalog is complete and true)."""
    problems: list[str] = []
    db = database_columns(conn, schema)
    documented = {t.name: t for t in sem.catalog.tables}

    for view in sorted(set(db) - set(documented)):
        problems.append(f"{view}: exposed by the database but missing from catalog.yml")
    for view in sorted(set(documented) - set(db)):
        problems.append(f"{view}: in catalog.yml but does not exist in the database")

    for view in sorted(set(db) & set(documented)):
        table = documented[view]
        cat_cols = {c.name: c for c in table.columns}
        for col in sorted(set(db[view]) - set(cat_cols)):
            problems.append(f"{view}.{col}: exposed by the database but missing from catalog.yml")
        for col in sorted(set(cat_cols) - set(db[view])):
            problems.append(f"{view}.{col}: in catalog.yml but not in the database (stale)")
        for col in sorted(set(db[view]) & set(cat_cols)):
            c = cat_cols[col]
            if c.type != db[view][col]:
                problems.append(f"{view}.{col}: catalog says {c.type}, database has "
                                f"{db[view][col]}")
            if PII_NAME.search(col) and not c.masked:
                problems.append(f"{view}.{col}: looks like personal data but is not marked "
                                "masked: true")
            if check_values and c.allowed_values:
                actual = {v for (v,) in conn.execute(
                    f'SELECT DISTINCT "{col}"::text FROM {view} WHERE "{col}" IS NOT NULL')}
                for v in sorted(actual - set(c.allowed_values)):
                    problems.append(f"{view}.{col}: value '{v}' exists in the data but is not "
                                    "in allowed_values")
    return problems
