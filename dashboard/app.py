import math
import os

import folium
import pandas as pd
import streamlit as st
from branca.colormap import LinearColormap
from branca.element import Element
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


# ─── Map helpers ─────────────────────────────────────────────────────────────

def _base_map() -> folium.Map:
    """Base Folium map + JS-injected 'reset view' control (no Streamlit rerun)."""
    m = folium.Map(
        location=list(MAP_CENTER),
        zoom_start=MAP_ZOOM,
        tiles="OpenStreetMap",
        control_scale=True,
    )

    reset_js = """
    (function() {
        var ResetControl = L.Control.extend({
            options: { position: 'topleft' },
            onAdd: function(map) {
                var c = L.DomUtil.create('div', 'leaflet-bar leaflet-control');
                var a = L.DomUtil.create('a', '', c);
                a.innerHTML = '\\uD83C\\uDFAF';
                a.title = 'Reset view';
                a.href = '#';
                a.style.cssText = 'font-size:16px;text-align:center;line-height:26px;width:26px;';
                L.DomEvent.on(a, 'click', function(e) {
                    L.DomEvent.stop(e);
                    map.setView(%(center)s, %(zoom)s);
                });
                return c;
            }
        });
        new ResetControl().addTo(%(map)s);
    })();
    """ % {"center": list(MAP_CENTER), "zoom": MAP_ZOOM, "map": m.get_name()}

    m.get_root().html.add_child(Element(reset_js))
    return m


def _radius_for(value: float, value_max: float,
                min_px: float = 4.0, max_px: float = 18.0) -> float:
    """Radius scaled on sqrt(value/max) so area is proportional to value."""
    if value_max <= 0:
        return min_px
    return min_px + (max_px - min_px) * math.sqrt(max(value, 0) / value_max)


# ─── Three maps ──────────────────────────────────────────────────────────────

def build_buffer_map(lockers: pd.DataFrame) -> folium.Map:
    """Tab 1 — naive 400 m buffer."""
    m = _base_map()
    vmax = float(lockers["pop_400m_buffer"].max() or 1)

    cmap = LinearColormap(
        colors=["#fee08b", "#fdae61", "#f46d43", "#d73027"],
        vmin=0, vmax=vmax,
        caption="Residents inside the 400 m buffer",
    )

    for _, r in lockers.iterrows():
        v = int(r["pop_400m_buffer"])
        folium.CircleMarker(
            location=[r["lat"], r["lon"]],
            radius=_radius_for(v, vmax),
            color="#333", weight=0.6, fill=True,
            fill_color=cmap(v), fill_opacity=0.80,
            tooltip=f"{r['locker_name']} — {v:,}",
            popup=folium.Popup(
                f"<b>{r['locker_name']}</b><br>"
                f"400 m buffer: <b>{v:,}</b>",
                max_width=240),
        ).add_to(m)
    cmap.add_to(m)
    return m


def build_network_map(lockers: pd.DataFrame, minutes: int) -> folium.Map:
    """Tab 2 — network-based accessibility for the chosen walk time."""
    col = f"pop_{minutes}min_network"
    m = _base_map()
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
    """Tab 3 — buffer vs 10-min network gap, blue↔red."""
    m = _base_map()
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
        "Each dot is an Omniva locker. Color and size both reflect the "
        "resident population inside a naive 400 m circle — **no streets, "
        "no barriers, no detours**."
    )
    st_folium(build_buffer_map(lockers), width=None, height=620,
              returned_objects=[])

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
        "Gap = (buffer − 10-min network) / buffer × 100. "
        "**Blue** = buffer under-estimates accessibility. "
        "**Red** = buffer over-estimates. Size scales with |gap|."
    )
    st_folium(build_gap_map(lockers), width=None, height=620,
              returned_objects=[])
