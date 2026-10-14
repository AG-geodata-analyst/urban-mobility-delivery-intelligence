# 🚚 Urban Mobility & Last-Mile Delivery Intelligence

> An Airflow-orchestrated data platform that ingests real public urban data — parcel-locker locations, public-transit schedules, population grids, and the walkable street network — computes **network-based walkability** for every locker using Dijkstra on the real pedestrian graph, and exposes the results on an interactive dashboard.

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Python 3.12](https://img.shields.io/badge/python-3.12-blue.svg)](https://www.python.org/downloads/release/python-3120/)
[![Airflow 3.1](https://img.shields.io/badge/airflow-3.1-017cee.svg)](https://airflow.apache.org/)
[![PostgreSQL 16](https://img.shields.io/badge/postgres-16-336791.svg)](https://www.postgresql.org/)
[![PostGIS 3.4](https://img.shields.io/badge/postgis-3.4-3E7AAB.svg)](https://postgis.net/)
[![Docker](https://img.shields.io/badge/docker-compose-2496ED.svg)](https://docs.docker.com/compose/)
[![OSMnx](https://img.shields.io/badge/osmnx-2.0-brightgreen.svg)](https://osmnx.readthedocs.io/)
[![Streamlit](https://img.shields.io/badge/streamlit-1.39-FF4B4B.svg)](https://streamlit.io/)
[![Status](https://img.shields.io/badge/status-phase--4--complete-success.svg)](#-roadmap)

[![Streamlit App](https://static.streamlit.io/badges/streamlit_badge_black_white.svg)](https://urban-mobility-delivery-intelligence.streamlit.app)

---

## 🌐 Live Demo

**→ [https://urban-mobility-delivery-intelligence.streamlit.app](https://urban-mobility-delivery-intelligence.streamlit.app)**

The deployed dashboard reads from a small static snapshot of the analytics output (~1.2 MB), so it runs with zero infrastructure — no database, no GDAL, no 62 MB walk graph — and boots in under 3 seconds.

> If the URL above is not live yet, run the deploy steps in the **Streamlit Cloud** section below. Every future `git push` to `main` auto-redeploys.

---

## 🎯 The Problem

Urban logistics — especially last-mile delivery — depends on two things cities already have: **people moving around** and **physical infrastructure placed where people are**. A parcel locker works well when it is close to where people live *and* easy to reach on foot.

But how do we actually measure whether a network of lockers is well-placed? A simple "400 m radius" analysis is quick but misleading: a river, a highway, or a railway line can turn a 400 m straight-line distance into a 15-minute walk. Real accessibility is a **network property**, not a geometric one.

This platform answers three questions:

1. **Where** are people, transit infrastructure, and parcel lockers in Tallinn?
2. **How walkable** is each locker, measured on the actual pedestrian network?
3. *(Version 2+)* **What will happen next** — which lockers will become capacity-constrained, and how should the network evolve?

### 🔑 Headline finding (Version 1)

> Across the **96** parcel lockers inside Tallinn's administrative boundary, a naive 400 m straight-line buffer **overestimates the population reachable in a 5-minute walk by an average of 9.9%**.

That gap is the analytical payoff: it quantifies how much a fast-but-wrong buffer analysis disagrees with a slower-but-correct network analysis. The gap is largest at lockers whose buffer crosses water, rail, or highways — where the naive straight-line distance reaches people that no pedestrian can actually get to.

The method is not one-directional: some central-Tallinn lockers show a **negative gap**, where the dense street grid reaches further on foot than the buffer suggests. That nuance is what makes the analysis defensible.

---

## 🌍 Why This Project

Most portfolio "last-mile" projects use synthetic data and a buffer analysis. This one uses **real public data** end-to-end and computes accessibility on the **real walkable street graph** via Dijkstra.

The difference between what a naive buffer says and what the network actually allows is the analytical payoff — and the dashboard makes that difference visible, one locker at a time.

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
                    (5 DAGs: 4 ingest + 1 compute)
                              │
                              ▼
                 PostgreSQL 16 + PostGIS 3.4
                    raw / staging / analytics / ops
                              │
                              ▼
                 Dijkstra isochrones on OSM walk graph
                    (5/10/15 min per locker)
                              │
                              ▼
                    analytics.locker_accessibility
                              │
                              ▼
              ┌───────────────┴───────────────┐
              ▼                               ▼
    scripts/export_dashboard_data.py    Local dev only
    (freeze → dashboard/data/)          (direct DB read)
              │
              ▼
     Interactive Streamlit dashboard
       (local + Streamlit Cloud)
```

### Airflow DAGs

| DAG | Type | Source → Target | Schedule |
|---|---|---|---|
| `ingest_parcel_lockers` | ingest | Omniva API → `raw.omniva_locations` | daily |
| `ingest_gtfs` | ingest | GTFS zip → `raw.gtfs_*` (4 tables) | weekly |
| `ingest_population` | ingest | WorldPop → `data/population/` + `raw.population_grid` | monthly |
| `ingest_osm_streets` | ingest | OpenStreetMap → `data/osm/tallinn_walk.graphml` | monthly |
| `compute_accessibility` | analytics | → `analytics.locker_accessibility` | daily 06:00 UTC |

---

## 📊 Data Currently Loaded

| Dataset | Rows / Size | Source |
|---|---|---|
| Parcel lockers (Omniva) | 1,449 (96 inside Tallinn polygon) | `omniva.ee/locations.json` |
| GTFS stops | 18,266 | Estonian unified GTFS |
| GTFS routes | 2,361 | same |
| GTFS trips | 93,712 | same |
| GTFS stop_times | 2,025,889 | same |
| Population raster | 21.4 M pixels (~1.27 M people) | WorldPop 100 m BSGM 2020 |
| Walkable street graph | 63,844 nodes · 171,854 edges · 62 MB | OSM via OSMnx |
| **Locker accessibility** | **96 lockers** | Dijkstra on the walk graph |

Every pipeline run is recorded in `ops.pipeline_runs`.

---

## 🔬 Accessibility Analysis (Phase 3)

For each locker **inside the Tallinn administrative polygon**:

1. **Snap** to the nearest node on the OSM pedestrian graph
2. **Run Dijkstra** with edge weight = `length / walking_speed`, where walking speed is **1.4 m/s** on streets and **0.8 m/s** on `highway=steps`
3. **Build isochrones** — convex hulls of all nodes reachable within 5, 10, and 15 minutes
4. **Sum WorldPop population** inside each isochrone via `rasterstats.zonal_stats`
5. **Also compute a naive 400 m buffer** for comparison
6. **Compute the gap**: `(buffer − network_5min) / buffer × 100`

### Results (96 Tallinn lockers)

| Metric | Value |
|---|---|
| Average population within a 5-minute walk | **1,075** |
| Average population within a 10-minute walk | **4,450** |
| Average population within a 15-minute walk | **9,877** |
| Average population inside a naive 400 m buffer | **1,200** |
| **Average buffer overestimate** | **9.9%** |

The method is not one-directional — some central-Tallinn lockers show a negative gap, where the dense street grid reaches further on foot than the straight-line buffer suggests. This nuance makes the analysis defensible.

---

## 📊 Interactive Dashboard (Phase 4)

A **Streamlit + Folium** dashboard turns the analytics table into a map-based story. Runs locally at `http://localhost:8503`, deployed live on Streamlit Cloud.

### Three tabs

| Tab | What it shows |
|---|---|
| **📍 Lockers & 400 m buffers** | Actual 400 m circles around each locker, coloured by buffer population. Optional WorldPop population grid (clipped to Tallinn), optional locker-location pins. |
| **🚶 Network accessibility (5 / 10 / 15 min)** | Dots sized and coloured by real network-based walkability. Radio toggle switches the walk-time threshold. |
| **📊 Buffer vs network gap** | Diverging blue↔red map showing where the naive buffer under- or over-estimates real accessibility. Size scales with \|gap\|. |

### Dashboard features

- **CartoDB Dark Matter** basemap via a free CARTO API key
- **Tallinn administrative boundary** outline + grey veil covering everything outside the study area
- **Layer switcher** (top-right): buffers (on), locker locations (off), population grid (off) — pure client-side, no page reloads
- **In-map reset button** (top-left) — recentres the map without rerunning Streamlit
- **Bottom-left legends** that appear/disappear with their layers, unified style: title + gradient bar + endpoints
- **KPI cards** at the top: lockers scored, avg 10-min walkable population, avg buffer population, avg gap
- **Zero infrastructure**: reads from a ~1.2 MB frozen snapshot (`dashboard/data/`), not from Postgres — so it runs anywhere Python runs

### Regenerating the snapshot

After re-running the pipeline, refresh the deployed dashboard's data with:

```bash
docker compose exec airflow-scheduler python /opt/airflow/scripts/export_dashboard_data.py
git add dashboard/data/
git commit -m "chore: refresh dashboard snapshot"
git push
```

Streamlit Cloud auto-redeploys within a minute.

---

## 🛠️ Tech Stack

| Layer | Technology |
|---|---|
| Orchestration | Apache Airflow 3.1.3 (LocalExecutor, Docker Compose) |
| Language | Python 3.12 |
| Database | PostgreSQL 16 + PostGIS 3.4 |
| Geospatial | GeoPandas · Shapely · pyproj · rasterio · rasterstats |
| Street networks | OSMnx 2.0 · NetworkX (Dijkstra) |
| Dashboard | Streamlit · Folium · streamlit-folium · Branca |
| External APIs | Omniva · Mobility Database (GTFS) · WorldPop · OSM Overpass |
| Containerisation | Docker Compose |
| Hosting | Streamlit Cloud (dashboard) |

---

## 📂 Project Structure

```
urban-mobility-delivery-intelligence/
├── dags/
│   ├── ingest_parcel_lockers.py       # Omniva locker locations
│   ├── ingest_gtfs.py                 # Tallinn GTFS (4 tables)
│   ├── ingest_population.py           # WorldPop 100 m raster
│   ├── ingest_osm_streets.py          # OSM walkable street graph
│   └── compute_accessibility.py       # Dijkstra accessibility
├── scripts/
│   ├── compute_accessibility.py       # Standalone Dijkstra + zonal stats
│   └── export_dashboard_data.py       # Freeze analytics → dashboard/data/
├── dashboard/
│   ├── app.py                         # Streamlit entry point (three tabs)
│   ├── requirements.txt               # Cloud-only deps (streamlit, folium, …)
│   └── data/                          # Frozen snapshot (~1.2 MB)
│       ├── lockers.csv
│       ├── tallinn.geojson
│       ├── network.geojson
│       ├── popgrid.png
│       └── popgrid_meta.json
├── src/utils/
│   └── reliability.py                 # Pipeline-run tracking + HTTP helpers
├── sql/
│   └── init-db.sql                    # raw / staging / analytics / ops schemas
├── docker/
│   └── Dockerfile                     # Airflow + GDAL + geospatial Python stack
├── data/                              # Cached downloads (gitignored except boundaries)
│   ├── boundaries/
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
- ~4 GB free RAM, ~2 GB free disk
- A free CARTO API key for the dark basemap — https://carto.com/basemaps/apikey/

### 1. Clone

```bash
git clone https://github.com/AG-geodata-analyst/urban-mobility-delivery-intelligence.git
cd urban-mobility-delivery-intelligence
```

### 2. Configure

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

echo "CARTO_API_KEY=cb1_your_key_here" >> .env

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
| Streamlit dashboard | http://localhost:8503 | *(none)* |
| Postgres | `localhost:5434` | `airflow` / `airflow` |

### 5. Run the full pipeline

```bash
# Ingestion
docker compose exec airflow-scheduler airflow dags test ingest_parcel_lockers $(date +%Y-%m-%d)
docker compose exec airflow-scheduler airflow dags test ingest_gtfs             $(date +%Y-%m-%d)
docker compose exec airflow-scheduler airflow dags test ingest_population       $(date +%Y-%m-%d)
docker compose exec airflow-scheduler airflow dags test ingest_osm_streets      $(date +%Y-%m-%d)

# Analytics
docker compose exec airflow-scheduler airflow dags test compute_accessibility   $(date +%Y-%m-%d)
```

### 6. Refresh the dashboard snapshot (only needed if the pipeline re-ran)

```bash
docker compose exec airflow-scheduler python /opt/airflow/scripts/export_dashboard_data.py
```

### 7. Inspect the results

```bash
docker compose exec postgres psql -U airflow -d airflow -c "
SELECT locker_name, pop_5min_network, pop_400m_buffer, buffer_network_gap
FROM analytics.locker_accessibility
ORDER BY buffer_network_gap DESC
LIMIT 10;"
```

---

## ☁️ Deploying the Dashboard to Streamlit Cloud

The dashboard has no infrastructure dependencies — a 1.2 MB static snapshot is bundled in the repo. Deployment is 15 minutes of clicking:

1. Sign in at https://share.streamlit.io with GitHub
2. **Create app** → point at this repo, branch `main`, file `dashboard/app.py`
3. **Advanced settings** → Python 3.12
4. **Secrets** → paste:

   ```toml
   CARTO_API_KEY = "cb1_your_key_here"
   ```

5. **Deploy**

Every `git push` to `main` triggers an automatic redeploy. No CI needed.

---

## 🗄️ Data Model

### Schemas

| Schema | Purpose |
|---|---|
| `raw` | Untyped landing zone — as-ingested data |
| `staging` | Typed, cleaned data (used in later phases) |
| `analytics` | Business-facing computed metrics |
| `ops` | Pipeline metadata — runs, timings, record counts |

### Key tables

| Table | Layer | Purpose |
|---|---|---|
| `raw.omniva_locations` | Raw | Parcel locker locations |
| `raw.gtfs_stops` / `_routes` / `_trips` / `_stop_times` | Raw | Public-transit schedule |
| `raw.population_grid` | Raw | Metadata for the WorldPop raster |
| **`analytics.locker_accessibility`** | **Analytics** | **Per-locker walkability** |
| `ops.pipeline_runs` | Ops | One row per DAG run |

---

## 🗺️ Roadmap

### Version 1 — Descriptive Network Intelligence *(shipped)*

- [x] **Phase 1** — Foundation (Docker Compose, Airflow 3.1.3, PostGIS 16)
- [x] **Phase 2** — Ingestion (Omniva · GTFS · WorldPop · OSM)
- [x] **Phase 3** — Network accessibility (Dijkstra isochrones + population zonal stats)
- [x] **Phase 4** — Interactive dashboard + documentation

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

Version 1 measures **walking accessibility only**. Transit-aware and multimodal routing (walk → wait → ride → walk) is deliberately deferred to Version 3, where the GTFS data already ingested here will be used in a full routing graph.

The distinction matters analytically:

- A **buffer** (400 m straight-line) is a rough approximation that ignores barriers.
- A **walking network isochrone** (5/10/15 min on the real pedestrian graph) is a defensible **lower bound** on how far someone can reach.
- A **multimodal isochrone** (walk + transit) is the realistic **upper bound** — but it requires modelling schedules and transfers, which is a V3-sized problem.

Version 1 computes the middle option and compares it against the first.

---

## 🧠 Design Decisions

| Decision | Rationale |
|---|---|
| **All data is real and public** | No synthetic places or routes — real Tallinn, real Omniva lockers, real GTFS |
| **Walk-only in V1** | Keeps scope tight and gives a defensible lower-bound metric |
| **Time-based edge weight** | Dijkstra cutoffs in seconds, not metres, so stairs slow the walk correctly |
| **Convex hull isochrones** | Fast and accurate enough at 5–15 min; concave hulls are a V2 upgrade |
| **Polygon filter for lockers** | Admin boundary, not just bounding box — eliminates lockers inside the bbox but outside Tallinn proper |
| **Frozen dashboard snapshot** | Decouples the dashboard from the DB, so the deployed app runs with zero infrastructure |
| **In-map Leaflet controls** | Reset button and layer switcher are pure JS — Streamlit never reruns on interaction |
| **Streamlit Cloud for hosting** | Free, auto-redeploys on push, no CI to maintain |

---

## 🙋 About

**Anderson Isaac Guamán Viveros**

Background in GIScience, Earth Observation, and Environmental Modelling. Currently focused on data engineering, data quality, and geospatial analytics.

- GitHub: [@AG-geodata-analyst](https://github.com/AG-geodata-analyst)

*Built with Apache Airflow, PostgreSQL + PostGIS, OSMnx, Streamlit, and real public urban data.*
