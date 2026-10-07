"""
Download the Tallinn walkable street network via OSMnx.

Saves the graph as GraphML in data/osm/, ready for Dijkstra-based
isochrone computation in Phase 3.

The graph contains pedestrian-accessible ways only (sidewalks, footpaths,
pedestrian streets) and is cached on disk so Phase 3 does not re-query
the Overpass API on every run.

Pipeline:
    start_run -> download -> finish_run
"""
import sys
from datetime import timedelta
from pathlib import Path

import pendulum
from airflow.sdk import dag, task

project_root = str(Path(__file__).resolve().parent.parent)
if project_root not in sys.path:
    sys.path.insert(0, project_root)

from src.utils import reliability as rel

DATA_DIR = Path(project_root) / "data" / "osm"
PLACE = "Tallinn, Estonia"


@dag(
    dag_id="ingest_osm_streets",
    start_date=pendulum.datetime(2024, 1, 1, tz="UTC"),
    schedule="@monthly",
    catchup=False,
    tags=["ingest", "osm", "phase-2"],
    default_args={
        "retries": 2,
        "retry_delay": timedelta(minutes=5),
        "retry_exponential_backoff": True,
    },
)
def ingest_osm_streets():

    @task
    def start_run(**kwargs) -> str:
        rel.start_pipeline_run(kwargs["run_id"], "ingest_osm_streets")
        return kwargs["run_id"]

    @task
    def download() -> dict:
        """
        Fetch the walkable street graph for Tallinn from OpenStreetMap.

        network_type="walk" restricts the download to pedestrian-accessible
        ways only. The graph is saved as GraphML so it can be reloaded
        without hitting the Overpass API again.
        """
        import osmnx as ox

        DATA_DIR.mkdir(parents=True, exist_ok=True)

        print(f"[download] Querying Overpass for '{PLACE}' walkable network...")
        graph = ox.graph_from_place(PLACE, network_type="walk", simplify=True)

        graph_path = DATA_DIR / "tallinn_walk.graphml"
        ox.save_graphml(graph, graph_path)

        nodes = graph.number_of_nodes()
        edges = graph.number_of_edges()
        size_mb = graph_path.stat().st_size / (1024 * 1024)

        print(
            f"[download] Saved {graph_path} "
            f"({nodes:,} nodes, {edges:,} edges, {size_mb:.1f} MB)"
        )
        return {
            "path": str(graph_path),
            "nodes": nodes,
            "edges": edges,
            "size_mb": round(size_mb, 2),
        }

    @task(trigger_rule="all_done")
    def finish_run(meta: dict, **kwargs) -> None:
        dag_run = kwargs.get("dag_run")
        status = "failed" if dag_run and dag_run.state == "failed" else "success"
        rel.finish_pipeline_run(
            dag_run_id=kwargs["run_id"],
            status=status,
            records_ingested=(meta or {}).get("nodes", 0),
        )

    run_id = start_run()
    meta   = download()

    run_id >> meta
    finish_run(meta)


ingest_osm_streets()
