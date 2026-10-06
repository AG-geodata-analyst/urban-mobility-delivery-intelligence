FROM apache/airflow:3.1.3-python3.12

USER root

# System libraries required by GDAL/rasterio/geopandas/osmnx.
RUN apt-get update && apt-get install -y --no-install-recommends \
    gdal-bin \
    libgdal-dev \
    libgeos-dev \
    libproj-dev \
    libspatialindex-dev \
    && rm -rf /var/lib/apt/lists/*

USER airflow

# Install geospatial Python packages as the airflow user.
# The Airflow image blocks pip-as-root on purpose: installing as root
# pollutes system site-packages and can break the airflow entry-point.
# These packages don't depend on apache-airflow, so this is safe.
RUN pip install --no-cache-dir \
    "rasterio>=1.3" \
    "geopandas>=0.14" \
    "osmnx>=1.9" \
    "networkx>=3.2" \
    "rasterstats>=0.19" \
    "shapely>=2.0" \
    "pyproj>=3.6" \
    "requests>=2.31"
