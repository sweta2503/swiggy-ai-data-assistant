"""`make dq` and `make dq-demo`."""
import argparse
import sys
from datetime import datetime
from pathlib import Path

from swiggy_ai.db import connect
from swiggy_ai.dq.runner import (
    DQReport,
    assert_gold_healthy,
    default_as_of,
    run_checks,
    run_corruption_demo,
)
from swiggy_ai.logging_setup import setup_logging

MARK = {"pass": "ok  ", "fail": "FAIL", "error": "ERR "}


def print_report(report: DQReport, only_problems: bool = False) -> None:
    for r in report.results:
        if only_problems and r.status == "pass":
            continue
        sev = "blocking" if r.check.severity == "blocking" else "warning "
        obs = "-" if r.observed is None else f"{r.observed:g}"
        thr = "-" if r.outcome is None else f"{r.outcome.threshold:g}"
        extra = f"  ({r.detail})" if r.detail and r.status != "pass" else ""
        print(f"  [{MARK[r.status]}] {sev} {r.check.name:<62} observed={obs:<8} max={thr}{extra}")
    fails = len(report.failures)
    print(f"  -> {len(report.results)} checks, {fails} not passing, "
          f"{len(report.blocking_failures)} blocking")


def main() -> int:
    setup_logging()
    p = argparse.ArgumentParser()
    p.add_argument("--demo", action="store_true", help="corrupt a copy of gold and show the checks")
    p.add_argument("--as-of", type=datetime.fromisoformat,
                   help="treat this timestamp as 'now' for freshness (default: end of data)")
    p.add_argument("--data-dir", type=Path, default=Path("data/raw"))
    a = p.parse_args()
    as_of = a.as_of or default_as_of(a.data_dir)

    if a.demo:
        # the demo creates a scratch schema, which the pipeline role is (rightly) not allowed to
        with connect("admin", autocommit=True) as admin:
            clean, corrupted, day = run_corruption_demo(admin, as_of)
        print(f"Copy of gold, as loaded: {len(clean.failures)} checks not passing")
        print(f"\nNow the newest day ({day}) is loaded a second time into the copy:")
        print_report(corrupted, only_problems=True)
        return 0 if corrupted.blocking_failures else 1  # the demo must catch it

    with connect("etl", autocommit=True) as conn:

        print(f"Running data quality checks (freshness measured at {as_of:%Y-%m-%d %H:%M %Z})")
        report = run_checks(conn, as_of=as_of)
        print_report(report)
        health = assert_gold_healthy(conn)
        print(f"Gold is {'HEALTHY' if health else 'NOT healthy'}: {health.message}")
        return 0 if report.ok else 1


if __name__ == "__main__":
    sys.exit(main())
