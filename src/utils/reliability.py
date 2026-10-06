"""
Shared utilities for all ingestion DAGs.

Two responsibilities:
  1. Track DAG runs in ops.pipeline_runs
  2. Provide a resilient HTTP fetch helper
"""
import json
from datetime import datetime, timezone
from pathlib import Path

import requests
from airflow.providers.postgres.hooks.postgres import PostgresHook

POSTGRES_CONN_ID = "postgres_urban"


def start_pipeline_run(dag_run_id: str, dag_id: str) -> int:
    """Insert a 'running' row. Returns the row id."""
    pg = PostgresHook(postgres_conn_id=POSTGRES_CONN_ID)
    with pg.get_conn() as conn, conn.cursor() as cur:
        cur.execute("""
            INSERT INTO ops.pipeline_runs (dag_run_id, dag_id, status, started_at)
            VALUES (%s, %s, 'running', NOW())
            RETURNING run_id;
        """, (dag_run_id, dag_id))
        run_id = cur.fetchone()[0]
    print(f"[monitoring] Started pipeline run #{run_id} ({dag_run_id})")
    return run_id


def finish_pipeline_run(
    dag_run_id: str,
    status: str,
    records_ingested: int = 0,
) -> None:
    """Update the run row with final status and counts."""
    pg = PostgresHook(postgres_conn_id=POSTGRES_CONN_ID)
    with pg.get_conn() as conn, conn.cursor() as cur:
        cur.execute("""
            UPDATE ops.pipeline_runs
            SET finished_at = NOW(),
                status = %s,
                records_ingested = %s
            WHERE dag_run_id = %s;
        """, (status, records_ingested, dag_run_id))
    print(f"[monitoring] Finished pipeline run {dag_run_id} → {status}")


def http_get_json(url: str, timeout: int = 30) -> dict | list:
    """Fetch a URL, parse JSON, raise on HTTP errors."""
    response = requests.get(url, timeout=timeout)
    response.raise_for_status()
    return response.json()


def http_download(url: str, dest: Path, timeout: int = 120) -> Path:
    """Stream-download a URL to a local file."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    with requests.get(url, timeout=timeout, stream=True) as r:
        r.raise_for_status()
        with dest.open("wb") as f:
            for chunk in r.iter_content(chunk_size=1 << 16):
                f.write(chunk)
    return dest


def project_root() -> Path:
    """Repo root, works both in Docker and locally."""
    return Path(__file__).resolve().parents[2]
