"""
Ingest the WorldPop 100m population raster for Estonia.

The GeoTIFF is stored on disk at data/population/ and its metadata is
recorded in raw.population_grid. Phase 3 reads the raster directly for
zonal statistics.

Product: Constrained 2020 UN-adjusted, 100m, BSGM variant.
  - Constrained = population restricted to built-up areas (recommended
    for urban accessibility analysis)
  - UN-adjusted  = totals match UN population estimates for Estonia
  - BSGM         = Built Settlement Growth Model dasymetric layer
"""
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

WORLDPOP_URL = (
    "https://data.worldpop.org/GIS/Population/"
    "Global_2000_2020_Constrained/2020/BSGM/EST/"
    "est_ppp_2020_UNadj_constrained.tif"
)
DATA_DIR = Path(project_root) / "data" / "population"
POSTGRES_CONN_ID = "postgres_urban"


@dag(
    dag_id="ingest_population",
    start_date=pendulum.datetime(2024, 1, 1, tz="UTC"),
    schedule="@monthly",
    catchup=False,
    tags=["ingest", "worldpop", "phase-2"],
    default_args={"retries": 2, "retry_delay": timedelta(minutes=5)},
)
def ingest_population():

    @task
    def start_run(**kwargs) -> str:
        rel.start_pipeline_run(kwargs["run_id"], "ingest_population")
        return kwargs["run_id"]

    @task
    def download() -> str:
        dest = DATA_DIR / "est_ppp_2020_UNadj_constrained.tif"
        rel.http_download(WORLDPOP_URL, dest, timeout=900)
        size_mb = dest.stat().st_size / (1024 * 1024)
        print(f"[download] Saved {dest} ({size_mb:.1f} MB)")
        return str(dest)

    @task
    def register_metadata(tif_path: str) -> dict:
        """Read the GeoTIFF and insert its bounds + resolution into Postgres."""
        import rasterio

        with rasterio.open(tif_path) as src:
            bounds = src.bounds
            crs = src.crs
            res_deg = src.res[0]          # pixel size in degrees
            raster_shape = src.shape
            nodata = src.nodata

        # WorldPop documents this product as "100m" — that's the nominal
        # pixel size at the equator. In EPSG:4326 the pixel is
        # 0.0008333° on each side; at Estonia's latitude (~59°N) that's
        # ~92m north-south and ~48m east-west. We record the documented
        # figure to match WorldPop's own documentation; the true
        # physical size is logged separately below.
        resolution_m = 100

        pg = PostgresHook(postgres_conn_id=POSTGRES_CONN_ID)
        with pg.get_conn() as conn, conn.cursor() as cur:
            cur.execute("TRUNCATE TABLE raw.population_grid;")
            cur.execute("""
                INSERT INTO raw.population_grid
                    (source_file, resolution_m, bounds)
                VALUES (%s, %s, %s);
            """, (
                Path(tif_path).name,
                resolution_m,
                f"EPSG:{crs.to_epsg() if crs else 'unknown'} | {bounds}",
            ))

        meta = {
            "file": Path(tif_path).name,
            "crs": str(crs),
            "resolution_m": resolution_m,
            "pixel_size_deg": res_deg,
            "shape": raster_shape,
            "nodata": nodata,
            "bounds": str(bounds),
        }
        print(f"[register] {meta}")
        return meta

    @task(trigger_rule="all_done")
    def finish_run(meta: dict, **kwargs) -> None:
        dag_run = kwargs.get("dag_run")
        status = "failed" if dag_run and dag_run.state == "failed" else "success"
        rel.finish_pipeline_run(
            dag_run_id=kwargs["run_id"],
            status=status,
            records_ingested=1 if meta else 0,
        )

    run_id = start_run()
    tif    = download()
    meta   = register_metadata(tif)

    run_id >> tif >> meta
    finish_run(meta)


ingest_population()
