import base64
import json
import math
import os
from io import BytesIO
from pathlib import Path

import folium
import numpy as np
import osmnx as ox
import pandas as pd
import rasterio
import streamlit as st
from branca.colormap import LinearColormap
from branca.element import Element, MacroElement
from jinja2 import Template
from PIL import Image
from rasterio.mask import mask as rio_mask
from shapely.geometry import box, mapping, shape
from sqlalchemy import create_engine, text
from streamlit_folium import st_folium


st.set_page_config(
    page_title="Tallinn Locker Accessibility",
    page_icon="📦",
    layout="wide",
)

DB_URL = (
    f"postgresql+psycopg2://"
    f"{os.getenv('DB_USER', 'airflow')}:{os.getenv('DB_PASS', 'airflow')}"
    f"@{os.getenv('DB_HOST', 'postgres')}:{os.getenv('DB_PORT', '5432')}"
    f"/{os.getenv('DB_NAME', 'airflow')}"
)

MAP_CENTER = (59.437, 24.753)
MAP_ZOOM = 11


# ─── DB ──────────────────────────────────────────────────────────────────────

@st.cache_resource
def get_engine():
    return create_engine(DB_URL, pool_pre_ping=True)


def _df_from_sql(sql: str) -> pd.DataFrame:
    with get_engine().connect() as conn:
        result = conn.execute(text(sql))
        return pd.DataFrame(result.fetchall(), columns=result.keys())


@st.cache_data(ttl=60)
def load_kpis() -> pd.DataFrame:
    return _df_from_sql("""
        SELECT
            COUNT(*)                                     AS lockers_scored,
            ROUND(AVG(pop_10min_network))::int           AS avg_net_10min,
            ROUND(AVG(pop_400m_buffer))::int             AS avg_buffer,
            ROUND(AVG(buffer_network_gap)::numeric, 1)   AS avg_gap
        FROM analytics.locker_accessibility;
    """)


@st.cache_data(ttl=60)
def load_lockers() -> pd.DataFrame:
    return _df_from_sql("""
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
    """)


@st.cache_data(show_spinner="Loading Tallinn boundary…")
def load_tallinn_boundary() -> dict:
    p = Path("/opt/airflow/data/boundaries/tallinn.geojson")
    if p.exists():
        return json.loads(p.read_text())
    gdf = ox.geocode_to_gdf("Tallinn, Estonia")
    return json.loads(gdf.to_json())


@st.cache_data(show_spinner="Loading walkable network…")
def load_network_edges(sample_every: int = 15) -> dict:
    G = ox.load_graphml("/opt/airflow/data/osm/tallinn_walk.graphml")
    edges = ox.graph_to_gdfs(G, nodes=False, edges=True)
    edges = edges.iloc[::sample_every]
    return json.loads(edges[["geometry"]].to_json())


@st.cache_data(show_spinner="Rendering population grid…")
def load_population_png():
    raster_path = "/opt/airflow/data/population/est_ppp_2020_UNadj_constrained.tif"
    bnd = load_tallinn_boundary()
    geom = [shape(bnd["features"][0]["geometry"])]

    with rasterio.open(raster_path) as src:
        out_image, out_transform = rio_mask(src, geom, crop=True)
        nodata = src.nodata
        height, width = out_image.shape[1], out_image.shape[2]
        west, south, east, north = rasterio.transform.array_bounds(
            height, width, out_transform
        )

    data = out_image[0].astype(float)
    if nodata is not None:
        data[data == nodata] = np.nan

    valid = np.isfinite(data) & (data > 0)
    if not valid.any():
        raise ValueError("Clipped raster has no valid population pixels.")

    vmax = float(np.percentile(data[valid], 99))

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

    buf = BytesIO()
    Image.fromarray(rgba, mode="RGBA").save(buf, format="PNG")
    png_b64 = base64.b64encode(buf.getvalue()).decode()

    return png_b64, [[south, west], [north, east]]


# ─── Map helpers ─────────────────────────────────────────────────────────────

def _outside_mask(boundary_geojson: dict) -> dict:
    geom = shape(boundary_geojson["features"][0]["geometry"])
    world = box(15.0, 55.0, 35.0, 65.0)
    return mapping(world.difference(geom))


class _ResetControl(MacroElement):
    """Leaflet control that recentres the map — pure JS, no Streamlit rerun."""

    _template = Template("""
        {% macro script(this, kwargs) %}
        (function() {
            var ResetControl = L.Control.extend({
                options: { position: 'topleft' },
                onAdd: function(map) {
                    var c = L.DomUtil.create('div', 'leaflet-bar leaflet-control');
                    var a = L.DomUtil.create('a', '', c);
                    a.innerHTML = '\\uD83C\\uDFAF Reset View';
                    a.title = 'Reset map to Tallinn centre';
                    a.href = '#';
                    a.style.cssText = 'font-size:12px;text-align:center;line-height:26px;'
                                    + 'height:26px;width:auto;padding:0 10px;'
                                    + 'text-decoration:none;color:#333;font-weight:500;';
                    L.DomEvent.on(a, 'click', function(e) {
                        L.DomEvent.stop(e);
                        map.setView({{ this.center }}, {{ this.zoom }});
                    });
                    return c;
                }
            });
            new ResetControl().addTo({{ this._parent.get_name() }});
        })();
        {% endmacro %}
    """)

    def __init__(self, center, zoom):
        super().__init__()
        self._name = "ResetControl"
        self.center = list(center)
        self.zoom = zoom


class _PopGridLegend(MacroElement):
    """Viridis legend, bottom-left, shown only when the WorldPop layer is active."""

    _template = Template("""
        {% macro script(this, kwargs) %}
        (function() {
            var map = {{ this._parent.get_name() }};

            var legendCss =
                "background:rgba(255,255,255,0.92);" +
                "padding:8px 10px;border-radius:4px;font-size:11px;" +
                "box-shadow:0 1px 4px rgba(0,0,0,0.35);display:none;";
            var barCss =
                "width:140px;height:12px;border-radius:2px;" +
                "background:linear-gradient(to right," +
                "#440154 0%,#3b528b 25%,#21918c 50%,#5ec962 75%,#fde725 100%);";

            var LegendControl = L.Control.extend({
                options: { position: 'bottomleft' },
                onAdd: function(m) {
                    var c = L.DomUtil.create('div', 'leaflet-control');
                    c.style.background = 'transparent';
                    c.style.border = 'none';
                    c.style.boxShadow = 'none';

                    var inner = document.createElement('div');
                    inner.id = 'pop-grid-legend-inner';
                    inner.style.cssText = legendCss;
                    inner.innerHTML =
                        '<div style="font-weight:600;margin-bottom:4px;color:#222;">' +
                        'Population density (WorldPop)</div>' +
                        '<div style="' + barCss + '"></div>' +
                        '<div style="display:flex;justify-content:space-between;' +
                        'font-size:10px;color:#444;margin-top:2px;">' +
                        '<span>low</span><span>high</span></div>';

                    c.appendChild(inner);
                    return c;
                }
            });
            new LegendControl().addTo(map);

            function toggleLegend(show) {
                var el = document.getElementById('pop-grid-legend-inner');
                if (el) el.style.display = show ? 'block' : 'none';
            }

            map.on('overlayadd', function(e) {
                if (e.name && e.name.indexOf('Population grid') !== -1) {
                    toggleLegend(true);
                }
            });
            map.on('overlayremove', function(e) {
                if (e.name && e.name.indexOf('Population grid') !== -1) {
                    toggleLegend(false);
                }
            });
        })();
        {% endmacro %}
    """)

    def __init__(self):
        super().__init__()
        self._name = "PopGridLegend"


def _base_map(show_network: bool = True,
              show_pop_grid: bool = False,
              prepend_layers: list | None = None) -> folium.Map:
    """Base Folium map with configurable overlays.

    prepend_layers are added BEFORE the population grid so they appear
    first in the layer switcher (in the same order as the list).
    """
    m = folium.Map(
        location=list(MAP_CENTER),
        zoom_start=MAP_ZOOM,
        tiles=None,
        control_scale=True,
    )

    carto_key = os.getenv("CARTO_API_KEY", "").strip()
    if carto_key:
        tiles_url = (
            "https://{s}.basemaps.cartocdn.com/dark_all/"
            "{z}/{x}/{y}{r}.png?key=" + carto_key
        )
        folium.TileLayer(
            tiles=tiles_url,
            name="CartoDB Dark Matter",
            attr="© OpenStreetMap contributors, © CARTO",
            opacity=0.95,
            detect_retina=True,
            control=False,
        ).add_to(m)
    else:
        folium.TileLayer(
            tiles="OpenStreetMap",
            name="OpenStreetMap",
            opacity=0.95,
            control=False,
        ).add_to(m)

    m.get_root().header.add_child(Element(
        "<style>"
        ".leaflet-bar a,"
        ".leaflet-bar a:hover,"
        ".leaflet-bar a:focus,"
        ".leaflet-bar a:active {"
        "  text-decoration: none !important;"
        "  outline: none !important;"
        "  box-shadow: none !important;"
        "}"
        "</style>"
    ))

    # --- layers that should appear FIRST in the switcher ---
    if prepend_layers:
        for layer in prepend_layers:
            layer.add_to(m)

    # --- population grid ---
    if show_pop_grid:
        try:
            png_b64, bounds = load_population_png()
            folium.raster_layers.ImageOverlay(
                image=f"data:image/png;base64,{png_b64}",
                bounds=bounds,
                opacity=0.75,
                name="Population grid (WorldPop)",
                interactive=False,
                show=False,
            ).add_to(m)
        except Exception as e:               # noqa: BLE001
            st.warning(f"Population grid unavailable: {e}")

    # --- walkable network (optional) ---
    if show_network:
        try:
            net = load_network_edges()
            folium.GeoJson(
                net,
                name="Walkable network (sampled)",
                style_function=lambda _: {
                    "color": "#000000",
                    "weight": 1,
                    "opacity": 0.9,
                },
                control=False,
            ).add_to(m)
        except Exception as e:                 # noqa: BLE001
            st.warning(f"Network overlay unavailable: {e}")

    # --- Tallinn boundary + outside mask ---
    try:
        bnd = load_tallinn_boundary()
        folium.GeoJson(
            bnd,
            name="Tallinn boundary",
            style_function=lambda _: {
                "color": "#000000",
                "weight": 1,
                "opacity": 1,
                "fill": False,
            },
            control=False,
        ).add_to(m)

        folium.GeoJson(
            _outside_mask(bnd),
            name="Outside study area",
            style_function=lambda _: {
                "fillColor": "#f2f2f2",
                "color": "#d0d0d0",
                "weight": 0.6,
                "fillOpacity": 0.75,
            },
            interactive=False,
            control=False,
        ).add_to(m)
    except Exception as e:                     # noqa: BLE001
        st.warning(f"Boundary / mask overlay unavailable: {e}")

    m.add_child(_ResetControl(MAP_CENTER, MAP_ZOOM))

    if show_pop_grid:
        m.add_child(_PopGridLegend())

    folium.LayerControl(collapsed=False, position="topright").add_to(m)
    return m


def _radius_for(value: float, value_max: float,
                min_px: float = 4.0, max_px: float = 18.0) -> float:
    if value_max <= 0:
        return min_px
    return min_px + (max_px - min_px) * math.sqrt(max(value, 0) / value_max)


# ─── Three maps ──────────────────────────────────────────────────────────────

def build_buffer_map(lockers: pd.DataFrame,
                     show_pop_grid: bool = False) -> folium.Map:
    """Tab 1 — naive 400 m buffer as actual geographic circles."""

    vmax = float(lockers["pop_400m_buffer"].max() or 1)
    cmap = LinearColormap(
        colors=["#ffffcc", "#fed976", "#fd8d3c", "#e31a1c", "#800026"],
        vmin=0, vmax=vmax,
        caption="Residents inside the 400 m buffer",
    )

    # --- FeatureGroup #1: buffers (checked by default) ---
    buf_fg = folium.FeatureGroup(name="400 m buffers", show=True)
    for _, r in lockers.iterrows():
        v = int(r["pop_400m_buffer"])
        folium.Circle(
            location=[r["lat"], r["lon"]],
            radius=400,
            color="#333333",
            weight=0.6,
            fill=True,
            fill_color=cmap(v),
            fill_opacity=0.80,
            tooltip=f"{r['locker_name']} — {v:,}",
            popup=folium.Popup(
                f"<b>{r['locker_name']}</b><br>"
                f"400 m buffer: <b>{v:,}</b>",
                max_width=240),
        ).add_to(buf_fg)

    # --- FeatureGroup #2: locker markers (unchecked by default) ---
    marker_fg = folium.FeatureGroup(name="Locker locations", show=False)
    for _, r in lockers.iterrows():
        folium.Marker(
            location=[r["lat"], r["lon"]],
            icon=folium.Icon(
                color="lightgray",
                icon="cube",
                prefix="fa",
            ),
            tooltip=f"{r['locker_name']}",
            popup=folium.Popup(
                f"<b>{r['locker_name']}</b><br>"
                f"400 m buffer: <b>{int(r['pop_400m_buffer']):,}</b>",
                max_width=240),
        ).add_to(marker_fg)

    m = _base_map(
        show_network=False,
        show_pop_grid=show_pop_grid,
        prepend_layers=[buf_fg, marker_fg],   # order in switcher: buffers, locations, grid
    )

    cmap.add_to(m)
    return m


def build_network_map(lockers: pd.DataFrame, minutes: int) -> folium.Map:
    col = f"pop_{minutes}min_network"
    m = _base_map(show_network=True, show_pop_grid=False)
    vmax = float(lockers[col].max() or 1)

    cmap = LinearColormap(
        colors=["#d73027", "#fc8d59", "#fee08b", "#a6d96a", "#1a9641"],
        vmin=0, vmax=vmax,
        caption=f"Residents reachable in {minutes} min on foot",
    )

    for _, r in lockers.iterrows():
        v = int(r[col])
        folium.CircleMarker(
            location=[r["lat"], r["lon"]],
            radius=_radius_for(v, vmax),
            color="#333", weight=0.6, fill=True,
            fill_color=cmap(v), fill_opacity=0.85,
            tooltip=f"{r['locker_name']} — {v:,}",
            popup=folium.Popup(
                f"<b>{r['locker_name']}</b><br>"
                f"{minutes}-min network: <b>{v:,}</b>",
                max_width=240),
        ).add_to(m)
    cmap.add_to(m)
    return m


def build_gap_map(lockers: pd.DataFrame) -> folium.Map:
    m = _base_map(show_network=True, show_pop_grid=False)
    gaps = lockers["buffer_network_gap"].astype(float)
    vmax_abs = float(gaps.abs().max() or 1)

    cmap = LinearColormap(
        colors=["#2166ac", "#67a9cf", "#f7f7f7", "#ef8a62", "#b2182b"],
        vmin=-vmax_abs, vmax=vmax_abs,
        caption="Gap (%) — blue: buffer underestimates · red: buffer overestimates",
    )

    for _, r in lockers.iterrows():
        g = float(r["buffer_network_gap"])
        folium.CircleMarker(
            location=[r["lat"], r["lon"]],
            radius=_radius_for(abs(g), vmax_abs, min_px=4.0, max_px=16.0),
            color="#333", weight=0.6, fill=True,
            fill_color=cmap(g), fill_opacity=0.85,
            tooltip=f"{r['locker_name']} — gap {g:.1f}%",
            popup=folium.Popup(
                f"<b>{r['locker_name']}</b><br>"
                f"Gap: <b>{g:.1f}%</b><br>"
                f"Buffer: {int(r['pop_400m_buffer']):,}<br>"
                f"10-min network: {int(r['pop_10min_network']):,}",
                max_width=260),
        ).add_to(m)
    cmap.add_to(m)
    return m


# ─── Render ──────────────────────────────────────────────────────────────────

st.title("📦 Tallinn Locker Accessibility")
st.caption(
    "Network-based walkability (Dijkstra on the OSM pedestrian graph) "
    "compared against a naive 400 m buffer."
)

row = load_kpis().iloc[0]

c1, c2, c3, c4 = st.columns(4)
c1.metric("Lockers scored",             f"{int(row['lockers_scored'])}")
c2.metric("Avg pop served (10 min)",    f"{int(row['avg_net_10min']):,}")
c3.metric("Avg pop in 400 m buffer",    f"{int(row['avg_buffer']):,}")
c4.metric("Avg buffer-network gap",     f"{row['avg_gap']:.1f}")

st.divider()

lockers = load_lockers()

tab_buffer, tab_network, tab_gap = st.tabs([
    "📍 Lockers & 400 m buffers",
    "🚶 Network accessibility (5 / 10 / 15 min)",
    "📊 Buffer vs network gap",
])

with tab_buffer:
    st.subheader("Naive 400 m straight-line buffer")
    st.caption(
        "Each circle is the actual 400 m footprint around one Omniva locker. "
        "Color reflects the resident population inside the circle — "
        "**no streets, no barriers, no detours**. "
        "Use the layer switcher at the top-right to toggle the buffers, "
        "locker locations, and the WorldPop population grid."
    )
    st_folium(
        build_buffer_map(lockers, show_pop_grid=True),
        width=None, height=620, returned_objects=[],
    )

with tab_network:
    st.subheader("Residents reachable on foot, by walk-time threshold")
    st.caption(
        "Dots show how many residents can actually walk to each locker "
        "within the selected time, following the real pedestrian network."
    )
    minutes = st.radio(
        "Walk-time threshold",
        options=[5, 10, 15],
        index=1,
        horizontal=True,
        format_func=lambda x: f"{x} min",
    )
    st_folium(build_network_map(lockers, minutes), width=None, height=620,
              returned_objects=[])

with tab_gap:
    st.subheader("How wrong is the naive 400 m buffer?")
    st.caption(
        "Gap = (buffer − 5-min network) / buffer × 100. "
        "**Blue** = buffer under-estimates accessibility. "
        "**Red** = buffer over-estimates. Size scales with |gap|."
    )
    st_folium(build_gap_map(lockers), width=None, height=620,
              returned_objects=[])
