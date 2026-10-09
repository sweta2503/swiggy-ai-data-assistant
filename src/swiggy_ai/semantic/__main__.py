"""`make catalog-check`."""
import sys

from swiggy_ai.db import connect
from swiggy_ai.semantic.check import check_catalog
from swiggy_ai.semantic.loader import SemanticError, load_semantic


def main() -> int:
    try:
        sem = load_semantic()
    except SemanticError as e:
        print("semantic files are invalid:")
        for p in e.problems:
            print(f"  - {p}")
        return 1
    with connect("etl", autocommit=True) as conn:
        problems = check_catalog(conn, sem)
    n_cols = sum(len(t.columns) for t in sem.catalog.tables)
    if problems:
        print(f"catalog does not match the database ({len(problems)} problems):")
        for p in problems:
            print(f"  - {p}")
        return 1
    print(f"catalog OK: {len(sem.catalog.tables)} views, {n_cols} columns, "
          f"{len(sem.metrics.metrics)} metrics, {len(sem.glossary)} glossary terms")
    return 0


if __name__ == "__main__":
    sys.exit(main())
