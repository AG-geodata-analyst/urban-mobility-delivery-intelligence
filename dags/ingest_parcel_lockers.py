"""
Ingest Omniva parcel-locker locations into raw.omniva_locations.

Source: https://www.omniva.ee/locations.json
Pipeline:
    start_run -> fetch -> load_raw -> finish_run
"""
import json
import sys
from datetime import timedelta
from pathlib import Path

import pendulum
from airflow.sdk import dag, task
from airflow.providers.postgres.hooks.postgres import PostgresHook

project_root = str(Path(__file__).resolve().parent.parent)
if project_root not in sys.path:
    sys.path.insert(0, project_root)

from src.utils import reliability as rel

OMNIVA_URL = "https://www.omniva.ee/locations.json"
DATA_DIR = Path(project_root) / "data" / "raw"
POSTGRES_CONN_ID = "postgres_urban"


@dag(
    dag_id="ingest_parcel_lockers",
    start_date=pendulum.datetime(2024, 1, 1, tz="UTC"),
    schedule="@daily",
    catchup=False,
    tags=["ingest", "omniva", "phase-2"],
    default_args={
        "retries": 3,
        "retry_delay": timedelta(minutes=2),
        "retry_exponential_backoff": True,
    },
)
def ingest_parcel_lockers():

    @task
    def start_run(**kwargs) -> str:
        rel.start_pipeline_run(kwargs["run_id"], "ingest_parcel_lockers")
        return kwargs["run_id"]

    @task
    def fetch() -> dict:
        """Download the Omniva JSON feed and cache it locally."""
        records = rel.http_get_json(OMNIVA_URL)
        raw_path = DATA_DIR / "omniva_locations.json"
        raw_path.parent.mkdir(parents=True, exist_ok=True)
        raw_path.write_text(json.dumps(records, indent=2, ensure_ascii=False))
        print(f"[fetch] Downloaded {len(records)} records → {raw_path}")
        return {"raw_path": str(raw_path), "count": len(records)}

    @task
    def load_raw(fetched: dict) -> int:
        """Parse the JSON and insert rows into raw.omniva_locations."""
        records = json.loads(Path(fetched["raw_path"]).read_text())
        pg = PostgresHook(postgres_conn_id=POSTGRES_CONN_ID)

        with pg.get_conn() as conn, conn.cursor() as cur:
            cur.execute("TRUNCATE TABLE raw.omniva_locations RESTART IDENTITY;")

            for r in records:
                cur.execute("""
                    INSERT INTO raw.omniva_locations
                        (name, zip, county, city, street, house_number,
                         x_coord, y_coord, raw_payload)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s);
                """, (
                    r.get("NAME"),
                    r.get("ZIP"),
                    r.get("A0_NAME"),
                    r.get("A1_NAME"),
                    r.get("A2_NAME"),
                    r.get("A3_NAME"),
                    _to_float(r.get("X_COORDINATE")),
                    _to_float(r.get("Y_COORDINATE")),
                    json.dumps(r),
                ))

        print(f"[load_raw] Inserted {len(records)} rows into raw.omniva_locations")
        return len(records)

    @task(trigger_rule="all_done")
    def finish_run(fetched: dict, loaded_count: int, **kwargs) -> None:
        dag_run = kwargs.get("dag_run")
        status = "failed" if dag_run and dag_run.state == "failed" else "success"
        rel.finish_pipeline_run(
            dag_run_id=kwargs["run_id"],
            status=status,
            records_ingested=loaded_count or 0,
        )

    run_id    = start_run()
    fetched   = fetch()
    loaded    = load_raw(fetched)

    run_id >> fetched >> loaded
    finish_run(fetched, loaded)


def _to_float(value):
    """Omniva sometimes returns coordinates as strings."""
    if value in (None, "", "NULL"):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


ingest_parcel_lockers()
