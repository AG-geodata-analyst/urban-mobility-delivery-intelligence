import math
import os

import folium
import pandas as pd
import streamlit as st
from branca.colormap import LinearColormap
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

# Green→red ramp used for both the dots and the legend
COLOR_RAMP = ["#d73027", "#fc8d59", "#fee08b", "#a6d96a", "#1a9641"]


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


def _radius_for(pop: int, pop_max: int) -> float:
    """Dot radius 4–16 px, scaled by sqrt(pop) so area ∝ population."""
    if pop_max <= 0:
        return 5.0
    return 4.0 + 12.0 * math.sqrt(max(pop, 0) / pop_max)


def build_map(lockers: pd.DataFrame) -> folium.Map:
    pop_max = int(lockers["pop_10min_network"].max() or 1)

    cmap = LinearColormap(
        colors=COLOR_RAMP,
        vmin=0,
        vmax=pop_max,
        caption="Residents reachable in a 10-minute walk",
    )

    m = folium.Map(
        location=list(MAP_CENTER),
        zoom_start=MAP_ZOOM,
        tiles="OpenStreetMap",
        control_scale=True,
    )

    for _, r in lockers.iterrows():
        pop10 = int(r["pop_10min_network"])
        color = cmap(pop10)
        folium.CircleMarker(
            location=[r["lat"], r["lon"]],
            radius=_radius_for(pop10, pop_max),
            color="#333333",
            weight=0.6,
            fill=True,
            fill_color=color,
            fill_opacity=0.85,
            tooltip=f"{r['locker_name']} — {pop10:,}",
            popup=folium.Popup(
                f"<b>{r['locker_name']}</b><br>"
                f"10-min network: <b>{pop10:,}</b><br>"
                f"5-min network: {int(r['pop_5min_network']):,}<br>"
                f"15-min network: {int(r['pop_15min_network']):,}<br>"
                f"400 m buffer: {int(r['pop_400m_buffer']):,}",
                max_width=280,
            ),
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

st.subheader("Where are the lockers, and how well-served is each one?")
st.caption(
    "Dot **color and size** both reflect residents reachable in a 10-minute walk. "
    "Bigger, greener dots = better-served. Hover or click for details."
)

# Reset-map-view button. Bumping the key forces Streamlit to remount the
# Folium component fresh at MAP_CENTER / MAP_ZOOM.
if "map_key" not in st.session_state:
    st.session_state.map_key = 0

col_btn, _ = st.columns([1, 5])
with col_btn:
    if st.button("🎯 Reset map view", use_container_width=True):
        st.session_state.map_key += 1

lockers = load_lockers()
fmap = build_map(lockers)

st_folium(
    fmap,
    key=f"locker_map_{st.session_state.map_key}",
    width=None,
    height=620,
    returned_objects=[],
)
