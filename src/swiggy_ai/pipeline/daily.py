"""`make daily`: the whole refresh, in order, with a stop sign before anything is published.

    bronze -> silver -> gold -> data quality -> (only if no blocking check failed) refresh the
    catalog and publish a new data version

"Publish" means two things here: the catalog is brought in line with the database, and the data
version goes up, which retires every cached answer. The gold tables themselves are updated in
place by the earlier stages, so a blocked run does not roll them back; what it does is withhold the
new version (so cached answers from the last good data stay valid), skip the catalog refresh, exit
with an error, and leave the failed checks where the assistant's data-quality warning reads them.

Every run is recorded in ops.pipeline_runs, including the ones that fail. Exit codes:
0 published, 1 blocked by data quality, 2 a stage failed, 3 another run is already in progress.
"""
import argparse
import logging
import sys
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import psycopg
from psycopg.types.json import Jsonb

from swiggy_ai.db import connect
from swiggy_ai.dq.runner import default_as_of, run_checks
from swiggy_ai.logging_setup import scrub, setup_logging
from swiggy_ai.ops import cache
from swiggy_ai.pipeline import bronze_loader, schema_drift
from swiggy_ai.pipeline.gold import run_gold
from swiggy_ai.pipeline.silver import run_silver
from swiggy_ai.semantic.refresh import refresh_catalog

log = logging.getLogger("daily")
LOCK_ID = 7_265_001  # one daily run at a time, across processes
EXIT = {"published": 0, "blocked": 1, "failed": 2}


@dataclass
class Stage:
    name: str
    status: str  # ok | failed | skipped
    seconds: float = 0.0
    detail: str = ""


@dataclass
class DailyRun:
    run_id: uuid.UUID
    trigger: str = "manual"
    status: str = "running"  # published | blocked | failed
    stages: list[Stage] = field(default_factory=list)
    changed: bool = False
    version_before: int | None = None
    version_after: int | None = None
    dq_checks: int = 0
    blocked_by: list[str] = field(default_factory=list)
    schema_alerts: int = 0
    catalog_changes: list[str] = field(default_factory=list)
    error: str = ""

    @property
    def exit_code(self) -> int:
        return EXIT.get(self.status, 2)


def _record_start(conn: psycopg.Connection, run: DailyRun) -> None:
    conn.execute("INSERT INTO ops.pipeline_runs (run_id, trigger, data_version_before)"
                 " VALUES (%s, %s, %s)", (run.run_id, run.trigger, run.version_before))


def _record_end(conn: psycopg.Connection, run: DailyRun) -> None:
    conn.execute(
        "UPDATE ops.pipeline_runs SET finished_at = now(), status = %s, changed = %s,"
        " stages = %s, dq_checks = %s, dq_blocking_failed = %s, schema_alerts_open = %s,"
        " catalog_changes = %s, data_version_after = %s, error = %s WHERE run_id = %s",
        (run.status, run.changed, Jsonb([s.__dict__ for s in run.stages]), run.dq_checks,
         Jsonb(run.blocked_by), run.schema_alerts, Jsonb(run.catalog_changes),
         run.version_after, scrub(run.error)[:500] or None, run.run_id))


def _stage(run: DailyRun, name: str, fn, say) -> Any:
    """Run one stage, time it, record it. An exception marks the run failed and is re-raised."""
    started = time.monotonic()
    try:
        value = fn()
    except Exception as e:
        run.stages.append(Stage(name, "failed", time.monotonic() - started,
                                f"{type(e).__name__}: {str(e)[:200]}"))
        run.status, run.error = "failed", f"{name}: {type(e).__name__}: {str(e)[:300]}"
        say(f"  {name:<9} FAILED  {run.error}")
        raise
    detail, result = value if isinstance(value, tuple) else ("", value)
    run.stages.append(Stage(name, "ok", time.monotonic() - started, detail))
    say(f"  {name:<9} ok      {detail}")
    return result


def run_daily(conn: psycopg.Connection, data_dir: Path = Path("data/raw"),
              trigger: str = "manual", catalog_dir: Path | None = None,
              aliases: schema_drift.Aliases | None = None, say=print) -> DailyRun:
    """Run everything. Never raises for a failed stage: the answer is in the returned run."""
    cache.ensure_cache_tables(conn)
    run = DailyRun(run_id=uuid.uuid4(), trigger=trigger,
                   version_before=cache.get_data_version(conn))
    previous = conn.execute("SELECT status FROM ops.pipeline_runs WHERE status <> 'running'"
                            " ORDER BY started_at DESC LIMIT 1").fetchone()
    _record_start(conn, run)
    say(f"daily run {str(run.run_id)[:8]} (data version {run.version_before})")
    try:
        bronze_loader.apply_ddl(conn)
        loaded = _stage(run, "bronze", lambda: _bronze(conn, data_dir, aliases), say)
        silver = _stage(run, "silver", lambda: _silver(conn), say)
        gold = _stage(run, "gold", lambda: _gold(conn, data_dir), say)
        run.changed = bool(loaded.files_loaded or silver.batches_processed
                           or any(gold.added.values()))
        report = _stage(run, "dq", lambda: _dq(conn, data_dir), say)
        run.dq_checks = len(report.results)
        run.schema_alerts = len(schema_drift.open_alerts(conn))

        if report.blocking_failures:
            run.blocked_by = [r.check.name for r in report.blocking_failures]
            run.status = "blocked"
            for name in ("catalog", "publish"):
                run.stages.append(Stage(name, "skipped", 0, "blocked by data quality"))
            say(f"  STOPPED: {len(run.blocked_by)} blocking data quality check(s) failed; "
                "nothing was published")
            for n in run.blocked_by[:10]:
                say(f"    - {n}")
        else:
            plan = _stage(run, "catalog", lambda: _catalog(conn, catalog_dir), say)
            run.catalog_changes = plan.lines()
            republish = run.changed or (previous is not None and previous[0] != "published")
            version = _stage(run, "publish", lambda: _publish(conn, run, republish), say)
            run.version_after = version
            run.status = "published"
        if run.schema_alerts:
            say(f"  WARNING: {run.schema_alerts} source file(s) are waiting in quarantine "
                "(see ops.schema_alerts); the rest of the data was loaded")
    except Exception as e:  # noqa: BLE001 - recorded by _stage; anything else is recorded here
        if run.status != "failed":
            run.status, run.error = "failed", f"{type(e).__name__}: {str(e)[:300]}"
        for name in ("silver", "gold", "dq", "catalog", "publish"):
            if not any(s.name == name for s in run.stages):
                run.stages.append(Stage(name, "skipped", 0, "an earlier stage failed"))
    finally:
        if run.version_after is None:
            run.version_after = run.version_before
        _record_end(conn, run)
    say(f"-> {run.status}"
        + (f" (data version {run.version_before} -> {run.version_after})"
           if run.version_after != run.version_before else " (data version unchanged)"))
    return run


def _bronze(conn, data_dir, aliases):
    r = bronze_loader.load_directory(conn, data_dir, aliases)
    return (f"{r.files_loaded} file(s) loaded, {r.files_skipped} already known, "
            f"{r.files_quarantined} quarantined, {r.total_rows:,} rows"), r


def _silver(conn):
    r = run_silver(conn)
    return f"{r.batches_processed} new batch(es)", r


def _gold(conn, data_dir):
    r = run_gold(conn, data_dir=data_dir)
    return f"{sum(r.added.values()):+,} rows across gold tables", r


def _dq(conn, data_dir):
    r = run_checks(conn, as_of=default_as_of(data_dir))
    return (f"{len(r.results)} checks, {len(r.failures)} not passing, "
            f"{len(r.blocking_failures)} blocking"), r


def _catalog(conn, catalog_dir):
    plan = refresh_catalog(conn, catalog_dir)
    notes = [f"{len(plan.lines())} note(s)"] if plan.lines() else ["catalog already matches"]
    return notes[0], plan


def _publish(conn, run: DailyRun, republish: bool) -> tuple[str, int]:
    if not republish:
        version = cache.get_data_version(conn)
        return f"nothing new, data version stays {version}", version
    version = cache.bump_data_version(conn, run.run_id)
    return f"data version {run.version_before} -> {version}; older cached answers retired", version


def publish_only(conn: psycopg.Connection, trigger: str = "airflow") -> DailyRun:
    """For orchestrators that run the stages as separate tasks: bump the version, but only if the
    latest data quality run is healthy."""
    from swiggy_ai.dq.runner import assert_gold_healthy
    cache.ensure_cache_tables(conn)
    run = DailyRun(run_id=uuid.uuid4(), trigger=trigger,
                   version_before=cache.get_data_version(conn))
    _record_start(conn, run)
    health = assert_gold_healthy(conn)
    if not health:
        run.status, run.blocked_by = "blocked", health.failed_checks or [health.message]
        run.version_after = run.version_before
    else:
        run.changed = True
        run.version_after = cache.bump_data_version(conn, run.run_id)
        run.status = "published"
    _record_end(conn, run)
    return run


def run_locked(trigger: str = "manual", data_dir: Path = Path("data/raw"), say=print
               ) -> DailyRun | None:
    """What the command line and the scheduler call: own connection, one run at a time."""
    with connect("etl", autocommit=True) as conn:
        if not conn.execute("SELECT pg_try_advisory_lock(%s)", (LOCK_ID,)).fetchone()[0]:
            say("another daily run is already in progress; not starting a second one")
            return None
        try:
            return run_daily(conn, data_dir, trigger, say=say)
        finally:
            conn.execute("SELECT pg_advisory_unlock(%s)", (LOCK_ID,))


def main(argv: list[str] | None = None) -> int:
    setup_logging()
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--data-dir", type=Path, default=Path("data/raw"))
    p.add_argument("--trigger", default="manual")
    p.add_argument("--publish-only", action="store_true",
                   help="only bump the data version (for Airflow), if data quality is healthy")
    a = p.parse_args(argv)
    if a.publish_only:
        with connect("etl", autocommit=True) as conn:
            run = publish_only(conn, a.trigger)
        print(f"publish: {run.status}"
              + (f" (blocked by {run.blocked_by})" if run.blocked_by else
                 f" (data version {run.version_before} -> {run.version_after})"))
        return run.exit_code
    run = run_locked(a.trigger, a.data_dir)
    return 3 if run is None else run.exit_code


if __name__ == "__main__":
    sys.exit(main())
