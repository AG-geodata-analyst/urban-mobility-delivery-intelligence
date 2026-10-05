-- Enable PostGIS for spatial data
CREATE EXTENSION IF NOT EXISTS postgis;
CREATE EXTENSION IF NOT EXISTS postgis_topology;

-- Data layers
CREATE SCHEMA IF NOT EXISTS raw;
CREATE SCHEMA IF NOT EXISTS staging;
CREATE SCHEMA IF NOT EXISTS analytics;
CREATE SCHEMA IF NOT EXISTS ops;

-- ============================================================
-- RAW LAYER — untyped landing zone
-- ============================================================

CREATE TABLE IF NOT EXISTS raw.omniva_locations (
    id            SERIAL PRIMARY KEY,
    name          TEXT,
    zip           TEXT,
    county        TEXT,
    city          TEXT,
    street        TEXT,
    house_number  TEXT,
    x_coord       NUMERIC,
    y_coord       NUMERIC,
    raw_payload   JSONB,
    ingested_at   TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS raw.gtfs_stops (
    stop_id     TEXT PRIMARY KEY,
    stop_name   TEXT,
    stop_lat    NUMERIC,
    stop_lon    NUMERIC,
    ingested_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS raw.gtfs_routes (
    route_id         TEXT PRIMARY KEY,
    route_short_name TEXT,
    route_long_name  TEXT,
    route_type       INTEGER,
    ingested_at      TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS raw.gtfs_trips (
    trip_id    TEXT PRIMARY KEY,
    route_id   TEXT,
    service_id TEXT,
    ingested_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS raw.gtfs_stop_times (
    trip_id        TEXT,
    stop_id        TEXT,
    arrival_time   TEXT,
    departure_time TEXT,
    stop_sequence  INTEGER,
    ingested_at    TIMESTAMPTZ DEFAULT NOW()
);

-- ============================================================
-- OPS LAYER — pipeline monitoring
-- ============================================================

CREATE TABLE IF NOT EXISTS ops.pipeline_runs (
    run_id           SERIAL PRIMARY KEY,
    dag_run_id       TEXT,
    dag_id           TEXT,
    started_at       TIMESTAMPTZ DEFAULT NOW(),
    finished_at      TIMESTAMPTZ,
    status           TEXT,
    records_ingested INTEGER DEFAULT 0
);

CREATE INDEX IF NOT EXISTS idx_pipeline_runs_started
    ON ops.pipeline_runs(started_at DESC);
