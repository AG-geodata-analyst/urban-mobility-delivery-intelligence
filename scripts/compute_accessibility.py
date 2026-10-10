"""
Compute network-based walkability for every Omniva parcel locker.

For each locker:
  1. Snap to nearest node on the OSM walking graph
  2. Run Dijkstra from that node (edge weight = walking time in seconds)
  3. Build isochrone polygons for 5, 10, 15 minutes
  4. Sum WorldPop population inside each isochrone
  5. Also compute the naive 400 m straight-line buffer population
  6. Compute the buffer-vs-network gap (percent overestimate)

Results are written to analytics.locker_accessibility.

Reads its DB connection from env vars (DB_HOST, DB_PORT, DB_USER,
DB_PASS, DB_NAME) so it can run standalone — PostgresHook requires an
Airflow task context and won't work from a plain python invocation.
"""
import os
import sys
from pathlib import Path

import numpy as np
import osmnx as ox
import networkx as nx
import psycopg2
from pyproj import Transformer
from rasterstats import zonal_stats
from shapely.geometry import Point, MultiPoint

project_root = Path(__file__).resolve().parents[1]

# ---------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------
GRAPH_PATH = project_root / "data" / "osm" / "tallinn_walk.graphml"
POP_RASTER = project_root / "data" / "population" / "est_ppp_2020_UNadj_constrained.tif"

WALK_SPEED_MPS = 1.4          # average walking speed on flat ground
WALK_SPEED_STAIRS_MPS = 0.8   # slower on stairs
BATCH_LIMIT = None            # set to a small int (e.g. 10) to test on a subset

# Tallinn + Harju commuter belt bounding box. The OSM walk graph
# covers the Tallinn administrative boundary; the bbox here is a
# little wider so any lockers in Viimsi, Maardu, Saue, or Keila that
# are reachable through the graph still get processed.
TALLINN_BBOX = {
    "lon_min": 24.30,
    "lon_max": 25.30,
    "lat_min": 59.20,
    "lat_max": 59.65,
}

# Minimum nodes needed to build a meaningful isochrone polygon.
MIN_NODES_FOR_POLYGON = 3

# ---------------------------------------------------------------
# DB connection helpers
# ---------------------------------------------------------------
def _db_conn():
    """Open a psycopg2 connection using env vars."""
    return psycopg2.connect(
        host=os.environ.get("DB_HOST", "postgres"),
        port=int(os.environ.get("DB_PORT", 5432)),
        user=os.environ.get("DB_USER", "airflow"),
        password=os.environ.get("DB_PASS", "airflow"),
        dbname=os.environ.get("DB_NAME", "airflow"),
    )


def main():
    print("[phase3] Loading OSM walk graph...")
    graph = ox.load_graphml(GRAPH_PATH)
    graph = _add_walk_times(graph)
    print(f"[phase3] Graph: {graph.number_of_nodes():,} nodes, "
          f"{graph.number_of_edges():,} edges")

    print("[phase3] Loading lockers from Postgres...")
    lockers = _load_lockers()
    print(f"[phase3] {len(lockers)} lockers to process")

    results = []
    for i, locker in enumerate(lockers, start=1):
        try:
            row = _process_locker(graph, locker)
            results.append(row)
            if i % 25 == 0 or i == len(lockers):
                print(f"[phase3] Processed {i}/{len(lockers)}")
        except Exception as e:
            print(f"[phase3] locker {locker['id']} failed: {e}")

    print(f"[phase3] Writing {len(results)} rows to analytics.locker_accessibility")
    _write_results(results)
    print("[phase3] Done.")


# ---------------------------------------------------------------
# Graph preparation
# ---------------------------------------------------------------
def _add_walk_times(graph) -> nx.MultiDiGraph:
    """Add a `walk_time` attribute (seconds) to every edge."""
    for u, v, k, data in graph.edges(keys=True, data=True):
        length = data.get("length") or 0
        highway = data.get("highway")
        if isinstance(highway, list):
            highway = highway[0] if highway else "unknown"

        speed = WALK_SPEED_STAIRS_MPS if highway == "steps" else WALK_SPEED_MPS
        data["walk_time"] = length / speed
    return graph


# ---------------------------------------------------------------
# Locker loading
# ---------------------------------------------------------------
def _load_lockers() -> list:
    """
    Read lockers from Postgres, restricted to the Tallinn bbox.

    The OSM walk graph covers Tallinn only. Lockers outside this region
    would snap onto the nearest Tallinn node hundreds of kilometres away
    and produce nonsense results, so we filter them out here.
    """
    with _db_conn() as conn, conn.cursor() as cur:
        cur.execute("""
            SELECT id, name, x_coord, y_coord
            FROM raw.omniva_locations
            WHERE x_coord BETWEEN %(lon_min)s AND %(lon_max)s
              AND y_coord BETWEEN %(lat_min)s AND %(lat_max)s
            ORDER BY id;
        """, TALLINN_BBOX)
        rows = cur.fetchall()

    if BATCH_LIMIT:
        rows = rows[:BATCH_LIMIT]

    records = [
        {"id": r[0], "name": r[1], "x": float(r[2]), "y": float(r[3])}
        for r in rows
    ]

    # Auto-detect CRS
    sample_x = records[0]["x"] if records else 0
    if abs(sample_x) < 180:
        print("[phase3] Locker coords appear to be WGS84 (no conversion)")
        for r in records:
            r["lon"], r["lat"] = r["x"], r["y"]
    else:
        print("[phase3] Converting locker coords from EPSG:3301 → EPSG:4326")
        transformer = Transformer.from_crs("EPSG:3301", "EPSG:4326", always_xy=True)
        for r in records:
            lon, lat = transformer.transform(r["x"], r["y"])
            r["lon"], r["lat"] = lon, lat
    return records

# ---------------------------------------------------------------
# Per-locker processing
# ---------------------------------------------------------------
def _process_locker(graph, locker: dict) -> dict:
    """Compute isochrones + population sums for one locker."""
    lon = locker["lon"]
    lat = locker["lat"]

    node = ox.distance.nearest_nodes(graph, X=lon, Y=lat)

    lengths = nx.single_source_dijkstra_path_length(
        graph, node, weight="walk_time", cutoff=15 * 60
    )

    node_coords = {
        n: (graph.nodes[n]["y"], graph.nodes[n]["x"]) for n in lengths
    }

    def pop_within(minutes: int) -> int:
        cutoff_s = minutes * 60
        points = [
            Point(lon_, lat_)
            for n, (lat_, lon_) in node_coords.items()
            if lengths[n] <= cutoff_s
        ]
        if len(points) < MIN_NODES_FOR_POLYGON:
            return 0
        hull = MultiPoint(points).convex_hull
        stats = zonal_stats([hull], str(POP_RASTER), stats=["sum"])
        return int(stats[0]["sum"] or 0)

    pop_5  = pop_within(5)
    pop_10 = pop_within(10)
    pop_15 = pop_within(15)

    # Naive 400 m buffer ≈ 400 / 111320 degrees latitude
    buffer_deg = 400 / 111_320
    buffer_geom = Point(lon, lat).buffer(buffer_deg)
    buf_stats = zonal_stats([buffer_geom], str(POP_RASTER), stats=["sum"])
    pop_buffer = int(buf_stats[0]["sum"] or 0)

    gap_pct = (
        round((pop_buffer - pop_5) / pop_buffer * 100, 2)
        if pop_buffer > 0 else 0.0
    )

    return {
        "locker_id": locker["id"],
        "locker_name": locker["name"],
        "x_coord": lon,
        "y_coord": lat,
        "pop_5min_network": pop_5,
        "pop_10min_network": pop_10,
        "pop_15min_network": pop_15,
        "pop_400m_buffer": pop_buffer,
        "buffer_network_gap": gap_pct,
    }


# ---------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------
def _write_results(rows: list) -> None:
    if not rows:
        print("[phase3] No results to write.")
        return

    with _db_conn() as conn, conn.cursor() as cur:
        cur.execute("TRUNCATE TABLE analytics.locker_accessibility;")
        for r in rows:
            cur.execute("""
                INSERT INTO analytics.locker_accessibility
                    (locker_id, locker_name, x_coord, y_coord,
                     pop_5min_network, pop_10min_network, pop_15min_network,
                     pop_400m_buffer, buffer_network_gap)
                VALUES (%(locker_id)s, %(locker_name)s, %(x_coord)s, %(y_coord)s,
                        %(pop_5min_network)s, %(pop_10min_network)s, %(pop_15min_network)s,
                        %(pop_400m_buffer)s, %(buffer_network_gap)s);
            """, r)


if __name__ == "__main__":
    main()
