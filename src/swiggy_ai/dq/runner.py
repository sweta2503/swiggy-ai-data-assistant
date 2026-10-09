"""Run the checks, store the results, and answer "is the data healthy?"."""
import json
import logging
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime, time, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import psycopg

from swiggy_ai.dq.checks import BLOCKING, Check, Context, Outcome, build_checks
from swiggy_ai.pipeline.runner import run_sql_dir

log = logging.getLogger("dq")
IST = ZoneInfo("Asia/Kolkata")


@dataclass
class CheckResult:
    check: Check
    status: str  # pass | fail | error
    outcome: Outcome | None
    error: str = ""

    @property
    def observed(self) -> float | None:
        return self.outcome.observed if self.outcome else None

    @property
    def detail(self) -> str:
        return self.error or (self.outcome.detail if self.outcome else "")


@dataclass
class DQReport:
    run_id: uuid.UUID
    run_kind: str
    run_ts: datetime
    results: list[CheckResult] = field(default_factory=list)

    @property
    def failures(self) -> list[CheckResult]:
        return [r for r in self.results if r.status != "pass"]

    @property
    def blocking_failures(self) -> list[CheckResult]:
        return [r for r in self.failures if r.check.severity == BLOCKING]

    @property
    def ok(self) -> bool:
        return not self.blocking_failures


def default_as_of(data_dir: Path | None = Path("data/raw")) -> datetime:
    """The 'now' used for freshness. Generated data stops on a fixed day, so for the demo we
    treat the end of that IST day as 'now'. With no generator metadata we use the real clock."""
    meta = data_dir / "_meta.json" if data_dir else None
    if meta and meta.exists():
        end = datetime.fromisoformat(json.loads(meta.read_text())["end_date"]).date()
        return datetime.combine(end, time(23, 59, 59), tzinfo=IST)
    return datetime.now(UTC)


def run_checks(conn: psycopg.Connection, checks: list[Check] | None = None,
               as_of: datetime | None = None, run_kind: str = "standard",
               store: bool = True) -> DQReport:
    """Run every check. One broken check never stops the others: it is reported as 'error'."""
    run_ts = datetime.now(UTC)
    report = DQReport(uuid.uuid4(), run_kind, run_ts)
    ctx = Context(as_of=as_of or default_as_of())
    for check in checks if checks is not None else build_checks():
        try:
            with conn.transaction():  # a failed query must not poison the next check
                outcome = check.run(conn, ctx)
            report.results.append(CheckResult(check, "pass" if outcome.passed else "fail", outcome))
        except Exception as e:  # noqa: BLE001 - report any failure of the check itself
            log.error("check %s could not run: %s", check.name, e)
            report.results.append(CheckResult(check, "error", None, str(e).splitlines()[0]))
    if store:
        _store(conn, report)
    return report


def _store(conn: psycopg.Connection, report: DQReport) -> None:
    run_sql_dir(conn, "ops")
    with conn.cursor() as cur:
        cur.executemany(
            "INSERT INTO ops.dq_results (run_id, run_kind, check_name, layer, table_name,"
            " severity, status, observed, threshold, detail, run_ts)"
            " VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
            [(report.run_id, report.run_kind, r.check.name, r.check.layer, r.check.table,
              r.check.severity, r.status, r.observed,
              r.outcome.threshold if r.outcome else None, r.detail, report.run_ts)
             for r in report.results])


# ---------------------------------------------------------------- health ------------------------
@dataclass
class Health:
    healthy: bool
    message: str
    failed_checks: list[str] = field(default_factory=list)
    run_id: uuid.UUID | None = None
    run_ts: datetime | None = None

    def __bool__(self) -> bool:
        return self.healthy


def assert_gold_healthy(conn: psycopg.Connection, max_age_hours: float | None = None) -> Health:
    """Is gold safe to answer from? Reads the latest standard DQ run (it does not re-run checks).

    False if any BLOCKING check failed or errored in that run, or if there is no run at all, or
    the run is older than `max_age_hours`. The message is ready to show to a user.
    """
    rows = conn.execute(
        "SELECT check_name, severity, status, run_id, run_ts FROM ops.dq_latest_run").fetchall()
    if not rows:
        return Health(False, "data quality warning: no data quality run has been recorded yet")
    run_id, run_ts = rows[0][3], rows[0][4]
    if max_age_hours is not None and datetime.now(UTC) - run_ts > timedelta(hours=max_age_hours):
        return Health(False, f"data quality warning: last check was more than {max_age_hours:g}"
                      " hours ago", run_id=run_id, run_ts=run_ts)
    failed = [name for name, sev, status, *_ in rows if sev == BLOCKING and status != "pass"]
    if failed:
        short = sorted({n.split(":")[0].replace("_", " ") for n in failed})
        return Health(False, f"data quality warning: {', '.join(short)} check failed",
                      failed, run_id, run_ts)
    return Health(True, "data quality checks passed", [], run_id, run_ts)


# ---------------------------------------------------------------- demo --------------------------
DEMO_SCHEMA = "gold_demo"
GOLD_TABLES = ["dim_date", "dim_city", "dim_restaurant", "dim_partner", "dim_customer",
               "fact_orders", "fact_order_items", "fact_refunds", "daily_city_sales"]


def run_corruption_demo(conn: psycopg.Connection, as_of: datetime | None = None
                        ) -> tuple[DQReport, DQReport, str]:
    """Copy gold to a scratch schema, 'load the newest day twice' there, and run the gold checks
    on the clean copy and on the corrupted copy. The real gold tables are never touched, and the
    runs are stored as run_kind='demo' so they cannot make the real data look unhealthy.
    Returns (clean report, corrupted report, the day that was duplicated)."""
    conn.execute(f"DROP SCHEMA IF EXISTS {DEMO_SCHEMA} CASCADE")
    conn.execute(f"CREATE SCHEMA {DEMO_SCHEMA}")
    for t in GOLD_TABLES:  # CREATE TABLE AS copies rows but not constraints, like a sloppy load
        conn.execute(f"CREATE TABLE {DEMO_SCHEMA}.{t} AS TABLE gold.{t}")
    try:
        checks = [c for c in build_checks(gold=DEMO_SCHEMA) if c.layer == "gold"]
        clean = run_checks(conn, checks, as_of, run_kind="demo")
        day = conn.execute(f"SELECT max(order_date_ist) FROM {DEMO_SCHEMA}.fact_orders"
                           ).fetchone()[0]
        conn.execute(f"INSERT INTO {DEMO_SCHEMA}.fact_orders SELECT * FROM"
                     f" {DEMO_SCHEMA}.fact_orders WHERE order_date_ist = %s", (day,))
        corrupted = run_checks(conn, checks, as_of, run_kind="demo")
    finally:
        conn.execute(f"DROP SCHEMA IF EXISTS {DEMO_SCHEMA} CASCADE")
    return clean, corrupted, day.isoformat()
