"""REFERENCE ONLY, FOR TEACHING. This file is not used by the project and is not run by `make ci`.

It shows how the same daily pipeline would look in Apache Airflow. Airflow is NOT installed in this
project's environment and is not a dependency: to try the file you would need your own Airflow
installation (2.4 or newer) with this repository mounted at PROJECT_DIR.

What it demonstrates
  * the same tasks and dependencies as `make daily`:
        bronze >> silver >> gold >> dq >> refresh_catalog >> publish
  * "stop before publishing if a blocking check fails" is just a failed task: `make dq` exits
    non-zero when a blocking check fails, so refresh_catalog and publish never start
  * one run at a time (max_active_runs=1) and retries only for the load stages

The project's own scheduler (`python -m swiggy_ai.pipeline.scheduler`, or cron) needs none of this.
"""
from datetime import datetime, timedelta

from airflow import DAG
from airflow.operators.bash import BashOperator

PROJECT_DIR = "/opt/swiggy-ai-data-assistant"  # where this repository is checked out

default_args = {
    "owner": "data-engineering",
    "retries": 1,
    "retry_delay": timedelta(minutes=5),
}

with DAG(
    dag_id="swiggy_daily",
    description="Bronze -> silver -> gold -> data quality -> catalog -> publish",
    default_args=default_args,
    start_date=datetime(2026, 1, 1),
    schedule="30 0 * * *",  # 06:00 India time is 00:30 UTC
    catchup=False,
    max_active_runs=1,
    tags=["swiggy", "reference"],
) as dag:

    def make(task_id: str, command: str, retries: int | None = None) -> BashOperator:
        return BashOperator(
            task_id=task_id,
            bash_command=f"cd {PROJECT_DIR} && {command}",
            **({} if retries is None else {"retries": retries}),
        )

    bronze = make("bronze", "make bronze")
    silver = make("silver", "make silver")
    gold = make("gold", "make gold")
    # No retries: a failed data quality check is a finding, not a glitch.
    dq = make("dq", "make dq", retries=0)
    refresh_catalog = make("refresh_catalog", "make refresh-catalog", retries=0)
    # Bumps the data version (retiring cached answers) only if the latest checks are healthy.
    publish = make(
        "publish", "python -m swiggy_ai.pipeline.daily --publish-only --trigger airflow", retries=0
    )

    bronze >> silver >> gold >> dq >> refresh_catalog >> publish
