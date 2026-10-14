"""
Freeze the dashboard's data into small static files under dashboard/data/.

Runs inside the Airflow container. Reads from Postgres (analytics table) and
from /opt/airflow/data (raster, boundary, OSM graph), then writes everything
the Streamlit app needs — no DB, no GDAL, no 62 MB graphml required at
deploy time.

Usage:
    docker compose exec airflow-scheduler python /opt/airflow/scripts/export_dashboard_data.py
"""
import json
import os
from pathlib import Path

import numpy as np
import osmnx as ox
import pandas as pd
import psycopg2
import rasterio
from PIL import Image
from rasterio.mask import mask as rio_mask
from shapely.geometry import shape


PROJECT_ROOT = Path("/opt/airflow")
OUT_DIR = PROJECT_ROOT / "dashboard" / "data"
OUT_DIR.mkdir(parents=True, exist_ok=True)

RASTER_PATH = PROJECT_ROOT / "data" / "population" / "est_ppp_2020_UNadj_constrained.tif"
GRAPH_PATH = PROJECT_ROOT / "data" / "osm" / "tallinn_walk.graphml"
BOUNDARY_PATH = PROJECT_ROOT / "data" / "boundaries" / "tallinn.geojson"

MAX_PNG_WIDTH = 2000          # downsample the raster so the PNG stays small
NETWORK_SAMPLE_EVERY = 40     # keep roughly 1/40 of the walk graph edges


def _db_conn():
    return psycopg2.connect(
        host=os.environ.get("DB_HOST", "postgres"),
        port=int(os.environ.get("DB_PORT", 5432)),
        user=os.environ.get("DB_USER", "airflow"),
        password=os.environ.get("DB_PASS", "airflow"),
        dbname=os.environ.get("DB_NAME", "airflow"),
    )


def export_lockers() -> None:
    sql = """
        SELECT
            locker_id,
            locker_name,
            x_coord::float  AS lon,
            y_coord::float  AS lat,
            pop_5min_network,
            pop_10min_network,
            pop_15min_network,
            pop_400m_buffer,
            buffer_network_gap
        FROM analytics.locker_accessibility
        ORDER BY locker_id;
    """
    with _db_conn() as conn:
        df = pd.read_sql(sql, conn)
    out = OUT_DIR / "lockers.csv"
    df.to_csv(out, index=False)
    print(f"[freeze] lockers.csv    → {len(df):>4} rows, {out.stat().st_size / 1024:.1f} KB")


def export_boundary() -> None:
    dst = OUT_DIR / "tallinn.geojson"
    dst.write_bytes(BOUNDARY_PATH.read_bytes())
    print(f"[freeze] tallinn.geojson → {dst.stat().st_size / 1024:.1f} KB")


def export_network() -> None:
    G = ox.load_graphml(GRAPH_PATH)
    edges = ox.graph_to_gdfs(G, nodes=False, edges=True)
    edges = edges.iloc[::NETWORK_SAMPLE_EVERY]
    geojson = json.loads(edges[["geometry"]].to_json())
    out = OUT_DIR / "network.geojson"
    # minified to keep file size down
    out.write_text(json.dumps(geojson, separators=(",", ":")))
    print(f"[freeze] network.geojson → {len(edges):>4} edges, "
          f"{out.stat().st_size / (1024 * 1024):.2f} MB")


def export_population_grid() -> None:
    bnd = json.loads(BOUNDARY_PATH.read_text())
    geom = [shape(bnd["features"][0]["geometry"])]

    with rasterio.open(RASTER_PATH) as src:
        out_image, out_transform = rio_mask(src, geom, crop=True)
        nodata = src.nodata
        h, w = out_image.shape[1], out_image.shape[2]
        west, south, east, north = rasterio.transform.array_bounds(
            h, w, out_transform
        )

    data = out_image[0].astype(float)
    if nodata is not None:
        data[data == nodata] = np.nan
    valid = np.isfinite(data) & (data > 0)
    if not valid.any():
        raise ValueError("Clipped raster has no valid population pixels.")

    vals = data[valid]
    stats = {
        "total": int(round(float(vals.sum()))),
        "low":   float(vals.min()),
        "high":  float(vals.max()),
    }

    vmax = float(np.percentile(vals, 99))
    anchors = np.array([
        [68, 1, 84], [59, 82, 139], [33, 145, 140],
        [94, 201, 98], [253, 231, 37],
    ], dtype=float)
    clipped = np.clip(np.nan_to_num(data) / max(vmax, 1e-9), 0, 1)
    idx = clipped * (len(anchors) - 1)
    lo = np.floor(idx).astype(int)
    hi = np.clip(lo + 1, 0, len(anchors) - 1)
    frac = (idx - lo)[..., None]
    rgb = anchors[lo] * (1 - frac) + anchors[hi] * frac
    rgba = np.zeros((*rgb.shape[:2], 4), dtype=np.uint8)
    rgba[..., :3] = rgb.astype(np.uint8)
    rgba[..., 3] = np.where(valid, 180, 0).astype(np.uint8)

    img = Image.fromarray(rgba, mode="RGBA")
    if img.width > MAX_PNG_WIDTH:
        ratio = MAX_PNG_WIDTH / img.width
        img = img.resize((MAX_PNG_WIDTH, int(img.height * ratio)), Image.LANCZOS)

    png_path = OUT_DIR / "popgrid.png"
    img.save(png_path, format="PNG", optimize=True)

    meta = {
        "bounds": [[south, west], [north, east]],
        "stats":  stats,
    }
    meta_path = OUT_DIR / "popgrid_meta.json"
    meta_path.write_text(json.dumps(meta, indent=2))

    print(f"[freeze] popgrid.png     → {img.width}×{img.height}, "
          f"{png_path.stat().st_size / 1024:.1f} KB")
    print(f"[freeze] popgrid_meta    → total={stats['total']:,} persons, "
          f"low={stats['low']:.2f}, high={stats['high']:.2f}")


def main() -> None:
    print(f"[freeze] writing to {OUT_DIR}")
    export_lockers()
    export_boundary()
    export_network()
    export_population_grid()
    print("[freeze] done.")


if __name__ == "__main__":
    main()
