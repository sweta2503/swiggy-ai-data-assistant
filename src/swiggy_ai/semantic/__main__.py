"""`make catalog-check`."""
import argparse
import sys

from swiggy_ai.db import connect
from swiggy_ai.semantic.check import check_catalog
from swiggy_ai.semantic.loader import SemanticError, load_semantic


def refresh(write: bool) -> int:
    from swiggy_ai.semantic.refresh import refresh_catalog
    with connect("etl", autocommit=True) as conn:
        plan = refresh_catalog(conn, write=write)
    for line in plan.lines():
        print(f"  {line}")
    if not plan.lines():
        print("catalog already matches the database")
    elif plan.changed:
        print("catalog.yml updated" if write else "(dry run: nothing written)")
    if plan.needs_description:
        print(f"{len(plan.needs_description)} column(s) still need a description in catalog.yml:")
        for c in plan.needs_description:
            print(f"  - {c}")
    return 1 if plan.needs_description or plan.new_views or plan.gone_views else 0


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--refresh", action="store_true", help="update catalog.yml from the database")
    p.add_argument("--dry-run", action="store_true", help="with --refresh: show, do not write")
    a = p.parse_args()
    if a.refresh:
        return refresh(write=not a.dry_run)
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
