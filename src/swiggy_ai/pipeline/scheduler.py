"""A small scheduler that runs the daily pipeline on a timetable.

    python -m swiggy_ai.pipeline.scheduler                  # every day at 06:00 India time
    python -m swiggy_ai.pipeline.scheduler --cron "30 5 * * *" --run-now

It is a long-running process: leave it running (a terminal, tmux, a service). If you would rather
not keep a process alive, plain cron does the same job with no code:

    0 6 * * *  cd /path/to/swiggy-ai-data-assistant && make daily >> logs/daily.log 2>&1

One run at a time is enforced by the pipeline itself (a database lock), so a slow run and the next
trigger cannot overlap.
"""
import argparse
import logging
import sys

from apscheduler.schedulers.base import BaseScheduler
from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.cron import CronTrigger

from swiggy_ai.logging_setup import setup_logging
from swiggy_ai.pipeline.daily import run_locked

log = logging.getLogger("scheduler")
DEFAULT_CRON = "0 6 * * *"
TIMEZONE = "Asia/Kolkata"


def job() -> int:
    """One scheduled run. A failure is logged, never raised: the scheduler must keep going."""
    try:
        run = run_locked(trigger="scheduler", say=log.info)
    except Exception:  # noqa: BLE001
        log.exception("the daily run crashed")
        return 2
    return 3 if run is None else run.exit_code


def build_scheduler(cron: str = DEFAULT_CRON, scheduler: BaseScheduler | None = None
                    ) -> BaseScheduler:
    """Raises ValueError for a cron expression that is not five fields."""
    trigger = CronTrigger.from_crontab(cron, timezone=TIMEZONE)
    sched = scheduler or BlockingScheduler(timezone=TIMEZONE)
    sched.add_job(job, trigger, id="daily", name="daily pipeline", max_instances=1,
                  coalesce=True, misfire_grace_time=3600, replace_existing=True)
    return sched


def main(argv: list[str] | None = None) -> int:
    setup_logging()
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--cron", default=DEFAULT_CRON, help="five-field cron, India time")
    p.add_argument("--run-now", action="store_true", help="also run once immediately")
    a = p.parse_args(argv)
    try:
        sched = build_scheduler(a.cron)
    except ValueError as e:
        print(f"not a valid cron expression ({a.cron!r}): {e}")
        return 1
    if a.run_now:
        job()
    next_run = sched.get_job("daily").trigger.get_next_fire_time(None, __import__(
        "datetime").datetime.now(sched.timezone))
    print(f"scheduler running: '{a.cron}' ({TIMEZONE}); next run {next_run:%Y-%m-%d %H:%M %Z}. "
          "Ctrl+C to stop.")
    try:
        sched.start()
    except (KeyboardInterrupt, SystemExit):
        print("scheduler stopped")
    return 0


if __name__ == "__main__":
    sys.exit(main())
