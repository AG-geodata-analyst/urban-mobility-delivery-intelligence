FROM apache/airflow:3.1.3-python3.12

# The base image already provides everything Phase 1 needs:
#   - apache-airflow 3.1.3
#   - apache-airflow-providers-postgres 6.4.0
#   - psycopg2-binary 2.9.11
#   - requests 2.32.5
#   - pandas, numpy, shapely, ...
# So we do NOT run pip install — that would rewrite the airflow entry-point
# with a broken shebang and break the image.
#
# We only add OS-level GDAL/GEOS libraries, which will be needed by
# geopandas/rasterio/osmnx in later phases.

USER root
RUN apt-get update && apt-get install -y --no-install-recommends \
    gdal-bin \
    libgdal-dev \
    libgeos-dev \
    && rm -rf /var/lib/apt/lists/*

USER airflow
