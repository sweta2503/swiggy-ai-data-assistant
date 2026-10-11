"""Phase 12, part 4: the scheduler and the reference Airflow DAG (the DAG is only read)."""
import ast
import re
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from swiggy_ai.pipeline import scheduler

ROOT = Path(__file__).resolve().parents[2]
DAG = ROOT / "orchestration" / "airflow" / "swiggy_daily_dag.py"


# --- scheduler ------------------------------------------------------------
def test_the_default_schedule_is_six_in_the_morning_india_time():
    sched = scheduler.build_scheduler()
    job = sched.get_job("daily")
    ist = ZoneInfo("Asia/Kolkata")
    nxt = job.trigger.get_next_fire_time(None, datetime(2026, 10, 11, 7, 0, tzinfo=ist))
    assert (nxt.hour, nxt.minute, nxt.day) == (6, 0, 12)
    before = job.trigger.get_next_fire_time(None, datetime(2026, 10, 11, 5, 0, tzinfo=ist))
    assert before.day == 11


def test_the_job_never_overlaps_itself_and_does_not_pile_up_missed_runs():
    job = scheduler.build_scheduler("30 5 * * *").get_job("daily")
    assert job.max_instances == 1 and job.coalesce is True and job.misfire_grace_time == 3600
    assert job.func is scheduler.job


def test_a_bad_cron_expression_is_rejected_with_a_message(capsys):
    with pytest.raises(ValueError):
        scheduler.build_scheduler("every day")
    assert scheduler.main(["--cron", "not a cron"]) == 1
    assert "not a valid cron" in capsys.readouterr().out


def test_the_job_returns_the_run_outcome_and_swallows_crashes(monkeypatch):
    from swiggy_ai.pipeline.daily import DailyRun
    monkeypatch.setattr(scheduler, "run_locked", lambda **k: DailyRun(run_id=None,
                                                                       status="blocked"))
    assert scheduler.job() == 1
    monkeypatch.setattr(scheduler, "run_locked", lambda **k: None)
    assert scheduler.job() == 3

    def boom(**k):
        raise RuntimeError("database is down")
    monkeypatch.setattr(scheduler, "run_locked", boom)
    assert scheduler.job() == 2  # the scheduler process stays alive


def test_the_scheduler_calls_the_pipeline_as_the_scheduler(monkeypatch):
    seen = {}
    monkeypatch.setattr(scheduler, "run_locked", lambda **k: seen.update(k))
    try:
        scheduler.job()
    except Exception:  # noqa: BLE001 - run_locked's stand-in returns None
        pass
    assert seen["trigger"] == "scheduler"


# --- the Airflow reference file ------------------------------------------------------------
def test_the_dag_says_loudly_that_it_is_reference_only():
    doc = ast.get_docstring(ast.parse(DAG.read_text()))
    assert "REFERENCE ONLY" in doc and "NOT installed" in doc and "TEACHING" in doc


def test_the_dag_has_the_same_tasks_and_order_as_make_daily():
    text = DAG.read_text()
    tasks = re.findall(r'make\(\s*"(\w+)"', text)
    assert tasks == ["bronze", "silver", "gold", "dq", "refresh_catalog", "publish"]
    assert "bronze >> silver >> gold >> dq >> refresh_catalog >> publish" in text
    assert "max_active_runs=1" in text and "catchup=False" in text


def test_every_command_the_dag_runs_exists_in_this_project():
    text = DAG.read_text()
    makefile = (ROOT / "Makefile").read_text()
    for target in re.findall(r'"make (\S+?)"', text):
        assert re.search(rf"^{re.escape(target)}:", makefile, re.M), target
    assert "swiggy_ai.pipeline.daily --publish-only" in text
    assert "--publish-only" in (ROOT / "src/swiggy_ai/pipeline/daily.py").read_text()


def test_the_dag_is_not_a_dependency_of_the_project():
    assert "airflow" not in (ROOT / "requirements.txt").read_text().lower()
    ast.parse(DAG.read_text())  # at least it is valid Python


def test_the_make_targets_exist():
    makefile = (ROOT / "Makefile").read_text()
    for target in ("daily", "scheduler", "cache-clear", "test-12"):
        assert re.search(rf"^{target}:", makefile, re.M), target
