"""
Run scripts/compute_accessibility.py as an orchestrated Airflow task.

This wraps the standalone accessibility script so it:
  - Runs on a daily schedule (after the ingestion DAGs)
  - Records its run in ops.pipeline_runs
  - Retries on transient failures
  - Appears alongside the other DAGs in the Airflow UI

Pipeline:
    start_run -> run_script -> finish_run
"""
import sys
from datetime import timedelta
from pathlib import Path

import pendulum
from airflow.sdk import dag, task
from airflow.providers.standard.operators.bash import BashOperator

project_root = str(Path(__file__).resolve().parent.parent)
if project_root not in sys.path:
    sys.path.insert(0, project_root)

from src.utils import reliability as rel


@dag(
    dag_id="compute_accessibility",
    start_date=pendulum.datetime(2024, 1, 1, tz="UTC"),
    schedule="0 6 * * *",          # 06:00 UTC daily
    catchup=False,
    tags=["analytics", "phase-3"],
    default_args={
        "retries": 1,
        "retry_delay": timedelta(minutes=10),
    },
)
def compute_accessibility():

    @task
    def start_run(**kwargs) -> str:
        rel.start_pipeline_run(kwargs["run_id"], "compute_accessibility")
        return kwargs["run_id"]

    run_script = BashOperator(
        task_id="run_accessibility_script",
        bash_command="cd /opt/airflow && python scripts/compute_accessibility.py",
    )

    @task(trigger_rule="all_done")
    def finish_run(**kwargs) -> None:
        dag_run = kwargs.get("dag_run")
        status = "failed" if dag_run and dag_run.state == "failed" else "success"
        rel.finish_pipeline_run(
            dag_run_id=kwargs["run_id"],
            status=status,
            records_ingested=0,
        )

    run_id = start_run()
    run_id >> run_script >> finish_run()


compute_accessibility()
