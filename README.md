# 🚚 Urban Mobility & Last-Mile Delivery Intelligence

> An Airflow-orchestrated data platform that ingests real public urban data — parcel-locker locations, public-transit schedules, population grids, and the walkable street network — to analyse the interaction between urban mobility, last-mile delivery, and accessibility.

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Python 3.12](https://img.shields.io/badge/python-3.12-blue.svg)](https://www.python.org/downloads/release/python-3120/)
[![Airflow 3.1](https://img.shields.io/badge/airflow-3.1-017cee.svg)](https://airflow.apache.org/)
[![PostgreSQL 16](https://img.shields.io/badge/postgres-16-336791.svg)](https://www.postgresql.org/)
[![PostGIS 3.4](https://img.shields.io/badge/postgis-3.4-3E7AAB.svg)](https://postgis.net/)
[![Docker](https://img.shields.io/badge/docker-compose-2496ED.svg)](https://docs.docker.com/compose/)
[![OSMnx](https://img.shields.io/badge/osmnx-2.0-brightgreen.svg)](https://osmnx.readthedocs.io/)
[![Status](https://img.shields.io/badge/status-phase--2--complete-informational.svg)](#-roadmap)

---

## 🎯 The Problem

Urban logistics — especially last-mile delivery — depends on two things that cities already have: **people moving around** and **physical infrastructure placed where people are**. Parcel lockers work well when they are close to where people live *and* easy to reach on foot or by public transport.

But how do we actually measure whether a network of lockers is well-placed? A simple "400 m radius" analysis is quick, but misleading: a river, a highway, or a railway line can turn a 400 m straight-line distance into a 15-minute walk. Real accessibility is a **network property**, not a geometric one.

This platform answers three questions:

1. **Where** are people, transit infrastructure, and parcel lockers in Tallinn?
2. **How walkable** is each locker, measured on the actual pedestrian network?
3. *(Version 2+)* **What will happen next** — which lockers will become capacity-constrained, and how should the network evolve?

## 🌍 Why This Project

Most portfolio "last-mile" projects use synthetic data and a buffer analysis. This one uses **real public data** end-to-end and computes accessibility on the **real walkable street graph** via Dijkstra. The difference between what a naive buffer says and what the network actually allows is the analytical payoff.

---

## 🏗️ Architecture

```
                    DATA SOURCES (all real, all public)
        ┌──────────────┬──────────────┬──────────────┬──────────────┐
        ▼              ▼              ▼              ▼
   Omniva API     Estonian GTFS    WorldPop       OpenStreetMap
   (lockers)      (transit)        100 m raster   (walk network)
        │              │              │              │
        └──────────────┴──────┬───────┴──────────────┘
                              ▼
                    Apache Airflow 3.1.3
                    (4 independent ingest DAGs)
                              │
                              ▼
                 PostgreSQL 16 + PostGIS 3.4
                    raw / staging / analytics / ops
                              │
                              ▼
                 [Phase 3 — accessibility analysis]
                 Dijkstra isochrones on OSM walk graph
                              │
                              ▼
                 [Phase 4 — Streamlit dashboard]
```

### Ingestion DAGs (Phase 2 — complete)

| DAG | Source | Target |
|---|---|---|
| `ingest_parcel_lockers` | Omniva `locations.json` API | `raw.omniva_locations` |
| `ingest_gtfs` | Estonian unified GTFS zip | `raw.gtfs_stops`, `raw.gtfs_routes`, `raw.gtfs_trips`, `raw.gtfs_stop_times` |
| `ingest_population` | WorldPop 100 m constrained raster | `raw.population_grid` + `data/population/*.tif` |
| `ingest_osm_streets` | OpenStreetMap via OSMnx | `data/osm/tallinn_walk.graphml` |

---

## 📊 What's Currently In the Database

Data as of the latest run of the four ingestion DAGs:

| Dataset | Rows / Size | Source |
|---|---|---|
| Parcel lockers (Omniva) | 1,449 | `omniva.ee/locations.json` |
| GTFS stops | 18,266 | Estonian unified GTFS |
| GTFS routes | 2,361 | same |
| GTFS trips | 93,712 | same |
| GTFS stop_times | 2,025,889 | same |
| Population raster | 21.4 M pixels (~1.27 M people) | WorldPop 100 m BSGM 2020 |
| Walkable street graph | 63,844 nodes · 171,854 edges · 62 MB | OpenStreetMap via OSMnx |

Every ingestion run is recorded in `ops.pipeline_runs` with status, record counts, and timestamps.

---

## 🛠️ Tech Stack

| Layer | Technology |
|---|---|
| Orchestration | Apache Airflow 3.1.3 (LocalExecutor, Docker Compose) |
| Language | Python 3.12 |
| Database | PostgreSQL 16 + PostGIS 3.4 |
| Geospatial | GeoPandas · Shapely · pyproj · rasterio · rasterstats |
| Street networks | OSMnx 2.0 · NetworkX |
| External APIs | Omniva · Mobility Database (GTFS) · WorldPop · OpenStreetMap Overpass |
| Containerisation | Docker Compose |

---

## 📂 Project Structure

```
urban-mobility-delivery-intelligence/
├── dags/
│   ├── ingest_parcel_lockers.py       # Omniva locker locations
│   ├── ingest_gtfs.py                 # Tallinn GTFS (4 tables)
│   ├── ingest_population.py           # WorldPop 100 m raster
│   └── ingest_osm_streets.py          # OSM walkable street graph
├── src/
│   └── utils/
│       └── reliability.py             # Pipeline-run tracking + HTTP helpers
├── sql/
│   └── init-db.sql                    # raw / staging / analytics / ops schemas
├── docker/
│   └── Dockerfile                     # Airflow + GDAL + geospatial Python stack
├── config/
│   └── simple_auth_manager_passwords.json.generated
├── data/                              # Cached downloads (gitignored)
│   ├── population/
│   └── osm/
├── docker-compose.yml
├── LICENSE
└── README.md
```

---

## 🚀 Running Locally

### Prerequisites

- Docker Desktop (WSL 2 backend if on Windows)
- ~4 GB free RAM
- ~2 GB free disk (for the OSM graph + raster)

### 1. Clone

```bash
git clone https://github.com/AG-geodata-analyst/urban-mobility-delivery-intelligence.git
cd urban-mobility-delivery-intelligence
```

### 2. Configure the environment

```bash
echo "AIRFLOW_UID=$(id -u)" > .env
cat >> .env << 'EOF'
AIRFLOW_PROJ_DIR=.
POSTGRES_USER=airflow
POSTGRES_PASSWORD=airflow
POSTGRES_DB=airflow
POSTGRES_PORT=5434
AIRFLOW_PORT=8082
EOF
```

Also create the Simple Auth Manager password file used by Airflow 3.x:

```bash
mkdir -p config
echo '{"admin": "admin"}' > config/simple_auth_manager_passwords.json.generated
```

### 3. Build and start

```bash
docker compose build
docker compose up airflow-init
docker compose up -d
```

### 4. Open the UIs

| Service | URL | Credentials |
|---|---|---|
| Airflow | http://localhost:8082 | `admin` / `admin` |
| Postgres | `localhost:5434` | `airflow` / `airflow` |

### 5. Run the ingestion pipelines

```bash
docker compose exec airflow-scheduler airflow dags test ingest_parcel_lockers $(date +%Y-%m-%d)
docker compose exec airflow-scheduler airflow dags test ingest_gtfs             $(date +%Y-%m-%d)
docker compose exec airflow-scheduler airflow dags test ingest_population       $(date +%Y-%m-%d)
docker compose exec airflow-scheduler airflow dags test ingest_osm_streets      $(date +%Y-%m-%d)
```

Each DAG records its run in `ops.pipeline_runs` and logs record counts.

### 6. Verify

```bash
docker compose exec postgres psql -U airflow -d airflow -c "
SELECT dag_id, status, records_ingested
FROM ops.pipeline_runs
ORDER BY started_at DESC LIMIT 10;"
```

---

## 🗄️ Data Model

### Schemas

| Schema | Purpose |
|---|---|
| `raw` | Untyped landing zone — as-ingested data |
| `staging` | Typed, cleaned, validated data (used in Phase 3) |
| `analytics` | Business-facing models and computed metrics |
| `ops` | Pipeline metadata — runs, timings, record counts |

### Tables

| Table | Layer | Purpose |
|---|---|---|
| `raw.omniva_locations` | Raw | Parcel locker locations (1,449 rows) |
| `raw.gtfs_stops` | Raw | Public-transit stops |
| `raw.gtfs_routes` | Raw | Transit routes |
| `raw.gtfs_trips` | Raw | Scheduled trips |
| `raw.gtfs_stop_times` | Raw | Stop-level schedule events |
| `raw.population_grid` | Raw | Metadata for the WorldPop raster |
| `ops.pipeline_runs` | Ops | One row per DAG run |

---

## 🗺️ Roadmap

### Version 1 — Descriptive Network Intelligence *(in progress)*

- [x] **Phase 1** — Foundation (Docker Compose, Airflow 3.1.3, PostGIS 16)
- [x] **Phase 2** — Ingestion (Omniva · GTFS · WorldPop · OSM)
- [ ] **Phase 3** — Network accessibility (Dijkstra isochrones + population zonal stats)
- [ ] **Phase 4** — Interactive dashboard + documentation

### Version 2 — Predictive Operations *(planned)*

- [ ] dbt models for mobility and parcel analytics
- [ ] Demand forecasting per locker
- [ ] Capacity-pressure KPIs and overload-risk alerts

### Version 3 — Decision Intelligence *(planned)*

- [ ] Multimodal routing (walk + GTFS transit)
- [ ] Scenario simulation ("add a locker here", "expand capacity there")
- [ ] Public decision-support dashboard

---

## 🎯 Scope of Version 1

Version 1 measures **walking accessibility only**. Transit-aware and multimodal routing (walk → wait → ride → walk) is deliberately deferred to Version 3, where it can be built on top of the GTFS data already ingested here.

The distinction matters analytically:

- A **buffer** (400 m straight-line) is a rough approximation that ignores barriers.
- A **walking network isochrone** (5/10/15 min on the real pedestrian graph) is a defensible lower bound on how far someone can reach.
- A **multimodal isochrone** (walk + transit) is the realistic upper bound — but it requires modelling schedules and transfers, which is a V3-sized problem.

Version 1 computes the middle option and compares it against the first.

---

## 🧠 Design Decisions

| Decision | Rationale |
|---|---|
| **All data is real and public** | No synthetic places or routes — real Tallinn, real Omniva lockers, real GTFS |
| **Walk-only in V1** | Keeps scope tight and gives a defensible lower-bound metric |
| **OSMnx cached to GraphML** | Phase 3 reloads instantly without re-querying Overpass |
| **BSGM-constrained population raster** | Only places population in built-up areas — no phantom residents in lakes or fields |
| **PostGIS enabled from day one** | Spatial joins are a first-class operation, not an add-on |
| **Airflow 3.1.3 with env-var init** | Uses the officially supported `_AIRFLOW_DB_MIGRATE` flow |
| **Installing geospatial libs as the airflow user** | The Airflow image blocks pip-as-root — this is the supported pattern |
| **Repo-relative paths everywhere** | DAGs run unchanged inside Docker and on any CI runner |

---

## 🙋 About

**Anderson Isaac Guamán Viveros**

Background in GIScience, Earth Observation, and Environmental Modelling. Currently focused on data engineering, data quality, and geospatial analytics.

- GitHub: [@AG-geodata-analyst](https://github.com/AG-geodata-analyst)

*Built with Apache Airflow, PostgreSQL + PostGIS, OSMnx, and real public urban data.*
