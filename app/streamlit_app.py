"""Executive dashboard for theme park ride uptime.

Design intent: a non-technical operations leader should be able to answer
"what is underperforming, and when" within about ten seconds of loading this.
Every chart is there to answer a specific operational question, and the
question is stated above the chart in plain language.
"""

from __future__ import annotations

import sys
from pathlib import Path

import duckdb
import pandas as pd
import plotly.express as px
import streamlit as st

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from src import config  # noqa: E402

st.set_page_config(
    page_title="Park Uptime Analytics",
    page_icon="🎢",
    layout="wide",
    initial_sidebar_state="expanded",
)

ACCENT = "#2B5CF0"
GOOD = "#1B8E5A"
WARN = "#D97706"
BAD = "#C0392B"


# ------------------------------------------------------------------ data
@st.cache_data(ttl=600)
def load_table(table: str) -> pd.DataFrame:
    if not config.WAREHOUSE_PATH.exists():
        return pd.DataFrame()
    con = duckdb.connect(str(config.WAREHOUSE_PATH), read_only=True)
    try:
        return con.execute(f"SELECT * FROM {table}").df()
    except Exception:
        return pd.DataFrame()
    finally:
        con.close()


def guard_empty() -> bool:
    if not config.WAREHOUSE_PATH.exists():
        st.error("No warehouse found.")
        st.markdown(
            "Build it first:\n"
            "```bash\n"
            "python scripts/seed_demo_data.py --days 60\n"
            "python -m src.load\n"
            "cd dbt && dbt run\n"
            "```"
        )
        return True
    return False


if guard_empty():
    st.stop()

rides = load_table("main_marts.dim_rides")
park_daily = load_table("main_marts.agg_park_daily")
ride_daily = load_table("main_marts.agg_ride_daily")
events = load_table("main_marts.fct_downtime_events")

if rides.empty or park_daily.empty:
    st.error("Warehouse exists but the marts are empty. Run `dbt run` inside /dbt.")
    st.stop()

park_daily["captured_date"] = pd.to_datetime(park_daily["captured_date"])
ride_daily["captured_date"] = pd.to_datetime(ride_daily["captured_date"])
events["downtime_date"] = pd.to_datetime(events["downtime_date"])

# ------------------------------------------------------------------ filters
st.sidebar.title("Filters")

all_parks = sorted(rides["park_name"].unique())
selected_parks = st.sidebar.multiselect("Parks", all_parks, default=all_parks)

min_date = park_daily["captured_date"].min().date()
max_date = park_daily["captured_date"].max().date()
default_start = max(min_date, (park_daily["captured_date"].max() - pd.Timedelta(days=29)).date())

date_range = st.sidebar.date_input(
    "Date range",
    value=(default_start, max_date),
    min_value=min_date,
    max_value=max_date,
)
if isinstance(date_range, tuple) and len(date_range) == 2:
    start_date, end_date = date_range
else:
    start_date, end_date = default_start, max_date

st.sidebar.caption(
    f"Data coverage: {min_date:%b %d, %Y} to {max_date:%b %d, %Y}"
)

if not selected_parks:
    st.warning("Select at least one park.")
    st.stop()

mask_park_daily = (
    park_daily["park_name"].isin(selected_parks)
    & (park_daily["captured_date"].dt.date >= start_date)
    & (park_daily["captured_date"].dt.date <= end_date)
)
mask_ride_daily = (
    ride_daily["park_name"].isin(selected_parks)
    & (ride_daily["captured_date"].dt.date >= start_date)
    & (ride_daily["captured_date"].dt.date <= end_date)
)
mask_events = (
    events["park_name"].isin(selected_parks)
    & (events["downtime_date"].dt.date >= start_date)
    & (events["downtime_date"].dt.date <= end_date)
)

pd_f = park_daily[mask_park_daily]
rd_f = ride_daily[mask_ride_daily]
ev_f = events[mask_events]
rides_f = rides[rides["park_name"].isin(selected_parks)]

# ------------------------------------------------------------------ header
st.title("Theme Park Uptime Analytics")
st.caption(
    "Attraction availability, breakdown patterns, and guest-impact prioritisation "
    "across four Walt Disney World parks."
)

# ------------------------------------------------------------------ KPI row
total_snapshots = rd_f["snapshots"].sum()
closed_snapshots = rd_f["closed_snapshots"].sum()
overall_uptime = 100 * (total_snapshots - closed_snapshots) / max(total_snapshots, 1)

# Prior period of equal length, for a real delta rather than a decorative one.
period_days = (end_date - start_date).days + 1
prior_start = start_date - pd.Timedelta(days=period_days)
prior_mask = (
    ride_daily["park_name"].isin(selected_parks)
    & (ride_daily["captured_date"].dt.date >= prior_start)
    & (ride_daily["captured_date"].dt.date < start_date)
)
prior = ride_daily[prior_mask]
if not prior.empty:
    prior_uptime = (
        100
        * (prior["snapshots"].sum() - prior["closed_snapshots"].sum())
        / max(prior["snapshots"].sum(), 1)
    )
    uptime_delta = f"{overall_uptime - prior_uptime:+.2f} pts vs prior period"
else:
    uptime_delta = None

c1, c2, c3, c4 = st.columns(4)
c1.metric("Fleet Uptime", f"{overall_uptime:.2f}%", uptime_delta)
c2.metric("Downtime Events", f"{len(ev_f):,}")
c3.metric("Downtime Hours Lost", f"{ev_f['downtime_hours'].sum():,.0f}")
c4.metric(
    "Avg Outage Length",
    f"{ev_f['downtime_minutes'].mean():.0f} min" if not ev_f.empty else "n/a",
)

st.divider()

# ------------------------------------------------------------------ trend
st.subheader("Is availability trending up or down?")
trend = (
    pd_f.groupby("captured_date", as_index=False)["park_uptime_pct"]
    .mean()
    .rename(columns={"park_uptime_pct": "uptime_pct"})
)
trend["rolling_7d"] = trend["uptime_pct"].rolling(7, min_periods=1).mean()

fig = px.line(
    trend,
    x="captured_date",
    y=["uptime_pct", "rolling_7d"],
    labels={"captured_date": "", "value": "Uptime %", "variable": ""},
)
fig.data[0].update(line=dict(color="#C7D0E0", width=1.5), name="Daily")
fig.data[1].update(line=dict(color=ACCENT, width=3), name="7-day average")
fig.update_layout(height=340, hovermode="x unified", margin=dict(t=10, b=0, l=0, r=0))
st.plotly_chart(fig, width="stretch")

# ------------------------------------------------------------------ priority
left, right = st.columns([3, 2])

with left:
    st.subheader("Which rides should maintenance prioritise?")
    st.caption(
        "Ranked by guest impact (downtime minutes weighted by typical standby wait), "
        "not raw downtime. An hour lost on a 90-minute-wait headliner affects far "
        "more guests than an hour on a walk-on."
    )
    top = rides_f.nlargest(12, "guest_impact_score").sort_values("guest_impact_score")
    fig2 = px.bar(
        top,
        x="guest_impact_score",
        y="ride_name",
        orientation="h",
        color="uptime_pct",
        color_continuous_scale=[[0, BAD], [0.5, WARN], [1, GOOD]],
        labels={
            "guest_impact_score": "Guest impact score",
            "ride_name": "",
            "uptime_pct": "Uptime %",
        },
        hover_data={"downtime_event_count": True, "avg_wait_when_open": True},
    )
    fig2.update_layout(height=460, margin=dict(t=10, b=0, l=0, r=0))
    st.plotly_chart(fig2, width="stretch")

with right:
    st.subheader("Park scorecard")
    scorecard = (
        pd_f.groupby("park_name")
        .agg(
            uptime_pct=("park_uptime_pct", "mean"),
            events=("total_downtime_events", "sum"),
            hours=("total_downtime_hours", "sum"),
        )
        .reset_index()
        .sort_values("uptime_pct")
    )
    scorecard["uptime_pct"] = scorecard["uptime_pct"].round(2)
    scorecard["hours"] = scorecard["hours"].round(1)
    st.dataframe(
        scorecard,
        width="stretch",
        hide_index=True,
        column_config={
            "park_name": "Park",
            "uptime_pct": st.column_config.ProgressColumn(
                "Uptime %", min_value=90, max_value=100, format="%.2f%%"
            ),
            "events": "Events",
            "hours": "Hours lost",
        },
    )

    st.subheader("Outage severity mix")
    if not ev_f.empty:
        sev = ev_f["severity_band"].value_counts().reset_index()
        sev.columns = ["severity_band", "count"]
        order = ["Brief (<30m)", "Moderate (30-60m)", "Significant (1-2h)", "Major (2h+)"]
        sev["severity_band"] = pd.Categorical(sev["severity_band"], order, ordered=True)
        sev = sev.sort_values("severity_band")
        fig3 = px.bar(
            sev,
            x="severity_band",
            y="count",
            labels={"severity_band": "", "count": "Events"},
            color_discrete_sequence=[ACCENT],
        )
        fig3.update_layout(height=250, margin=dict(t=10, b=0, l=0, r=0))
        st.plotly_chart(fig3, width="stretch")

st.divider()

# ------------------------------------------------------------------ when
st.subheader("When do breakdowns cluster?")
st.caption(
    "Staffing question: if outages concentrate in specific hours or days, "
    "technician coverage should follow that pattern rather than being flat."
)

if not ev_f.empty:
    heat = (
        ev_f.groupby(["started_day_name", "started_hour"])
        .size()
        .reset_index(name="events")
    )
    day_order = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
    pivot = (
        heat.pivot(index="started_day_name", columns="started_hour", values="events")
        .reindex(day_order)
        .fillna(0)
    )
    fig4 = px.imshow(
        pivot,
        labels=dict(x="Hour of day", y="", color="Events"),
        color_continuous_scale=["#F7F6F2", ACCENT],
        aspect="auto",
    )
    fig4.update_layout(height=320, margin=dict(t=10, b=0, l=0, r=0))
    st.plotly_chart(fig4, width="stretch")

# ------------------------------------------------------------------ detail
with st.expander("Ride-level detail table"):
    detail = rides_f[
        [
            "ride_name",
            "park_name",
            "land_name",
            "uptime_pct",
            "downtime_event_count",
            "avg_downtime_minutes",
            "longest_downtime_minutes",
            "avg_wait_when_open",
            "guest_impact_score",
        ]
    ].sort_values("guest_impact_score", ascending=False)
    st.dataframe(detail, width="stretch", hide_index=True)
    st.download_button(
        "Download as CSV",
        detail.to_csv(index=False).encode(),
        "ride_reliability.csv",
        "text/csv",
    )

st.caption(
    "Source: Queue-Times community API. Pipeline: Python ingestion -> DuckDB -> dbt -> Streamlit."
)
