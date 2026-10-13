import os

import pandas as pd
import streamlit as st
from sqlalchemy import create_engine

from sqlalchemy import text

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


@st.cache_resource
def get_engine():
    return create_engine(DB_URL, pool_pre_ping=True)


@st.cache_data(ttl=60)
def load_kpis() -> pd.DataFrame:
    sql = """
        SELECT
            COUNT(*)                                     AS lockers_scored,
            ROUND(AVG(pop_10min_network))::int           AS avg_net_10min,
            ROUND(AVG(pop_400m_buffer))::int             AS avg_buffer,
            ROUND(AVG(buffer_network_gap)::numeric, 1)   AS avg_gap
        FROM analytics.locker_accessibility;
    """
    with get_engine().connect() as conn:
        result = conn.execute(text(sql))
        return pd.DataFrame(result.fetchall(), columns=result.keys())

st.title("📦 Tallinn Locker Accessibility")
st.caption(
    "Network-based walkability (Dijkstra on the OSM pedestrian graph) "
    "compared against a naive 400 m buffer."
)

df = load_kpis()
row = df.iloc[0]

c1, c2, c3, c4 = st.columns(4)
c1.metric("Lockers scored",             f"{int(row['lockers_scored'])}")
c2.metric("Avg pop served (10 min)",    f"{int(row['avg_net_10min']):,}")
c3.metric("Avg pop in 400 m buffer",    f"{int(row['avg_buffer']):,}")
c4.metric("Avg buffer-network gap",     f"{row['avg_gap']:.1f}")

st.divider()
st.info(
    "Skeleton live — map, locker detail, and error distribution coming next."
)
