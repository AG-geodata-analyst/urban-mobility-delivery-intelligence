"""
Ingest Tallinn GTFS data into raw.gtfs_* tables.

Source: https://eu-gtfs.remix.com/estonia_unified_gtfs.zip

GTFS is the universal standard for public-transit data. The zip contains
four CSVs we care about:
  stops.txt       -> raw.gtfs_stops
  routes.txt      -> raw.gtfs_routes
  trips.txt       -> raw.gtfs_trips
  stop_times.txt  -> raw.gtfs_stop_times

Pipeline:
    start_run -> download -> load_stops / load_routes / load_trips / load_stop_times
              -> finish_run
"""
import csv
import io
import sys
import zipfile
from datetime import timedelta
from pathlib import Path

import pendulum
from airflow.sdk import dag, task
from airflow.providers.postgres.hooks.postgres import PostgresHook

project_root = str(Path(__file__).resolve().parent.parent)
if project_root not in sys.path:
    sys.path.insert(0, project_root)

from src.utils import reliability as rel

GTFS_URL = "https://eu-gtfs.remix.com/estonia_unified_gtfs.zip"
DATA_DIR = Path(project_root) / "data" / "raw"
POSTGRES_CONN_ID = "postgres_urban"


@dag(
    dag_id="ingest_gtfs",
    start_date=pendulum.datetime(2024, 1, 1, tz="UTC"),
    schedule="@weekly",
    catchup=False,
    tags=["ingest", "gtfs", "phase-2"],
    default_args={"retries": 2, "retry_delay": timedelta(minutes=3)},
)
def ingest_gtfs():

    @task
    def start_run(**kwargs) -> str:
        rel.start_pipeline_run(kwargs["run_id"], "ingest_gtfs")
        return kwargs["run_id"]

    @task
    def download() -> str:
        """Download the GTFS zip and cache it locally."""
        zip_path = DATA_DIR / "estonia_unified_gtfs.zip"
        rel.http_download(GTFS_URL, zip_path, timeout=600)
        size_mb = zip_path.stat().st_size / (1024 * 1024)
        print(f"[download] Saved {zip_path} ({size_mb:.1f} MB)")
        return str(zip_path)

    @task
    def load_stops(zip_path: str) -> int:
        return _load_gtfs_table(
            zip_path, "stops.txt", "raw.gtfs_stops",
            columns=["stop_id", "stop_name", "stop_lat", "stop_lon"],
            source_fields=["stop_id", "stop_name", "stop_lat", "stop_lon"],
        )

    @task
    def load_routes(zip_path: str) -> int:
        return _load_gtfs_table(
            zip_path, "routes.txt", "raw.gtfs_routes",
            columns=["route_id", "route_short_name", "route_long_name", "route_type"],
            source_fields=["route_id", "route_short_name", "route_long_name", "route_type"],
        )

    @task
    def load_trips(zip_path: str) -> int:
        return _load_gtfs_table(
            zip_path, "trips.txt", "raw.gtfs_trips",
            columns=["trip_id", "route_id", "service_id"],
            source_fields=["trip_id", "route_id", "service_id"],
        )

    @task
    def load_stop_times(zip_path: str) -> int:
        return _load_gtfs_table(
            zip_path, "stop_times.txt", "raw.gtfs_stop_times",
            columns=["trip_id", "stop_id", "arrival_time", "departure_time", "stop_sequence"],
            source_fields=["trip_id", "stop_id", "arrival_time", "departure_time", "stop_sequence"],
        )

    @task(trigger_rule="all_done")
    def finish_run(counts: dict, **kwargs) -> None:
        dag_run = kwargs.get("dag_run")
        status = "failed" if dag_run and dag_run.state == "failed" else "success"
        total = sum(counts.values()) if counts else 0
        rel.finish_pipeline_run(
            dag_run_id=kwargs["run_id"],
            status=status,
            records_ingested=total,
        )

    run_id   = start_run()
    zip_path = download()

    stops      = load_stops(zip_path)
    routes     = load_routes(zip_path)
    trips      = load_trips(zip_path)
    stop_times = load_stop_times(zip_path)

    counts = {
        "stops": stops,
        "routes": routes,
        "trips": trips,
        "stop_times": stop_times,
    }

    run_id >> zip_path
    zip_path >> [stops, routes, trips, stop_times]
    finish_run(counts)


def _load_gtfs_table(zip_path: str, filename: str, table: str,
                     columns: list, source_fields: list) -> int:
    """Extract one CSV from the zip, read it, and bulk-insert into the target table."""
    pg = PostgresHook(postgres_conn_id=POSTGRES_CONN_ID)

    rows = []
    with zipfile.ZipFile(zip_path) as zf:
        with zf.open(filename) as raw:
            # GTFS files are sometimes UTF-8-BOM; utf-8-sig strips that
            text = io.TextIOWrapper(raw, encoding="utf-8-sig")
            reader = csv.DictReader(text)
            for r in reader:
                rows.append(tuple((r.get(f) or None) for f in source_fields))

    with pg.get_conn() as conn, conn.cursor() as cur:
        cur.execute(f"TRUNCATE TABLE {table};")
        placeholders = ", ".join(["%s"] * len(columns))
        cur.executemany(
            f"INSERT INTO {table} ({', '.join(columns)}) VALUES ({placeholders});",
            rows,
        )

    print(f"[load] {table}: {len(rows)} rows")
    return len(rows)


ingest_gtfs()
