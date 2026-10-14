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
def test_the_dag_says_how_to_run_it_and_that_airflow_is_optional():
    doc = ast.get_docstring(ast.parse(DAG.read_text()))
    assert "make airflow" in doc and "http://localhost:8080" in doc
    assert "NOT installed" in doc and "not a dependency" in doc


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


# --- the Airflow service that runs the DAG ------------------------------------------------------
import yaml  # noqa: E402

COMPOSE = yaml.safe_load((ROOT / "docker-compose.yml").read_text())


def test_airflow_is_an_optional_local_only_service_that_runs_this_dag():
    svc = COMPOSE["services"]["airflow"]
    assert svc["profiles"] == ["airflow"]  # `make up` does not start it
    assert svc["ports"] == ["127.0.0.1:8080:8080"]  # never exposed on the network
    assert svc["depends_on"]["postgres"]["condition"] == "service_healthy"
    assert "./orchestration/airflow:/opt/airflow/dags:ro" in svc["volumes"]
    assert ".:/opt/swiggy-ai-data-assistant" in svc["volumes"]  # the DAG's PROJECT_DIR
    assert "/opt/swiggy-ai-data-assistant" in DAG.read_text()


def test_inside_docker_the_tasks_reach_the_database_by_its_service_name():
    env = COMPOSE["services"]["airflow"]["environment"]
    for key in ("DATABASE_URL_ADMIN", "DATABASE_URL_ETL", "DATABASE_URL_ASSISTANT"):
        assert "@postgres:5432/swiggy" in env[key] and "localhost" not in env[key]
    assert env["PY"] == "/opt/swiggy-venv/bin/python"  # the Makefile's `PY ?=` picks this up
    assert "PY ?=" in (ROOT / "Makefile").read_text()


def test_the_airflow_image_is_pinned_and_keeps_the_projects_packages_in_their_own_venv():
    dockerfile = (ROOT / "orchestration" / "airflow" / "Dockerfile").read_text()
    base = re.search(r"^FROM (\S+)", dockerfile, re.M).group(1)
    assert base.startswith("apache/airflow:2.") and "latest" not in base
    assert COMPOSE["services"]["airflow"]["image"].startswith("swiggy-airflow:")
    assert "/opt/swiggy-venv" in dockerfile and "make" in dockerfile
    assert "airflow" not in (ROOT / "requirements.txt").read_text().lower()


def test_the_airflow_task_packages_match_the_projects_pinned_versions():
    mine = {}
    for line in (ROOT / "requirements.txt").read_text().splitlines():
        m = re.match(r"([A-Za-z0-9_.\-]+(?:\[\w+\])?)==([\w.]+)", line)
        if m:
            mine[m.group(1).lower()] = m.group(2)
    theirs = (ROOT / "orchestration" / "airflow" / "requirements.txt").read_text().splitlines()
    pinned = [re.match(r"([\w.\-]+(?:\[\w+\])?)==([\w.]+)", x) for x in theirs if "==" in x]
    assert pinned
    for m in pinned:
        assert mine[m.group(1).lower()] == m.group(2), m.group(1)


def test_the_publish_task_uses_the_projects_python_not_airflows():
    text = DAG.read_text()
    assert "${PY:-python}" in text and "PYTHONPATH=src:." in text


def test_the_airflow_make_targets_exist_and_are_documented():
    makefile = (ROOT / "Makefile").read_text()
    for target in ("airflow", "airflow-down"):
        assert re.search(rf"^{target}:", makefile, re.M), target
    assert "--profile airflow" in makefile
    assert "make airflow" in (ROOT / "README.md").read_text()


def test_airflow_reset_can_only_remove_airflows_own_volume():
    makefile = (ROOT / "Makefile").read_text()
    recipe = makefile[makefile.index("airflow-reset:"):makefile.index("check:")]
    assert "label=com.docker.compose.volume=swiggy_airflow_data" in recipe
    assert "swiggy_pgdata" not in recipe and "down -v" not in recipe
    assert "make airflow-reset" in (ROOT / "README.md").read_text()
