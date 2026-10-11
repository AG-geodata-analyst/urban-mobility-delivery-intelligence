import os

import pandas as pd
import plotly.express as px
import psycopg2
import streamlit as st

st.set_page_config(
    page_title="Tallinn Locker Accessibility",
    page_icon="📦",
    layout="wide",
)

DB_HOST = os.getenv("DB_HOST", "postgres")
DB_PORT = os.getenv("DB_PORT", "5432")
DB_USER = os.getenv("DB_USER", "airflow")
DB_PASS = os.getenv("DB_PASS", "airflow")
DB_NAME = os.getenv("DB_NAME", "airflow")


@st.cache_data(ttl=300)
def load_data() -> pd.DataFrame:
    """Read the accessibility results via psycopg2 directly.

    Pandas 2.x + SQLAlchemy 1.4 do not cooperate: pd.read_sql calls
    .cursor() on whatever it gets, which neither the Engine nor the
    Connection from SQLAlchemy 1.4 exposes. Using psycopg2 bypasses
    that mismatch entirely.
    """
    conn = psycopg2.connect(
        host=DB_HOST,
        port=int(DB_PORT),
        user=DB_USER,
        password=DB_PASS,
        dbname=DB_NAME,
    )
    try:
        return pd.read_sql(
            "SELECT * FROM analytics.locker_accessibility ORDER BY locker_name",
            conn,
        )
    finally:
        conn.close()

# ---------------------------------------------------------------

st.title("📦 Tallinn Locker Accessibility")
st.caption(
    "Network-based walkability (Dijkstra on the OSM pedestrian graph) "
    "compared against a naive 400 m buffer."
)

df = load_data()

if df.empty:
    st.info("No accessibility data yet. Run the compute_accessibility DAG first.")
    st.stop()

# ------------------------------------------------------------------
# KPI cards
# ------------------------------------------------------------------
c1, c2, c3, c4 = st.columns(4)
c1.metric("Lockers analysed", f"{len(df):,}")
c2.metric("Avg pop within 5 min walk", f"{int(df['pop_5min_network'].mean()):,}")
c3.metric("Avg pop in 400 m buffer", f"{int(df['pop_400m_buffer'].mean()):,}")
c4.metric(
    "Median buffer overestimate",
    f"{df['buffer_network_gap'].median():.1f}%",
)

st.divider()

# ------------------------------------------------------------------
# Map — colored by buffer_network_gap
# ------------------------------------------------------------------
fig = px.scatter_map(
    df,
    lat="y_coord",
    lon="x_coord",
    color="buffer_network_gap",
    size="pop_5min_network",
    hover_name="locker_name",
    hover_data={
        "pop_5min_network": True,
        "pop_400m_buffer": True,
        "buffer_network_gap": ":.1f",
        "x_coord": False,
        "y_coord": False,
    },
    color_continuous_scale="RdYlGn_r",
    range_color=(-50, 100),
    zoom=10,
    center={"lat": 59.4370, "lon": 24.7536},   # 👈 Tallinn city centre
    height=620,
    title="Lockers colored by buffer-vs-network gap (red = buffer overestimates)",
)
fig.update_layout(
    map_style="carto-darkmatter",
    margin=dict(l=0, r=0, t=40, b=0),
)
st.plotly_chart(fig, use_container_width=True)

# ------------------------------------------------------------------
# Top 10 most overestimated
# ------------------------------------------------------------------
st.subheader("Top 10 lockers where the buffer overestimates walkability most")
top = df.nlargest(10, "buffer_network_gap")[
    ["locker_name", "pop_5min_network", "pop_400m_buffer", "buffer_network_gap"]
]
st.dataframe(top, use_container_width=True, hide_index=True)

# ------------------------------------------------------------------
# Full table
# ------------------------------------------------------------------
st.subheader("All lockers")
st.dataframe(df, use_container_width=True, hide_index=True)
