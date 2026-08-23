# Theme Park Uptime Analytics

An end-to-end analytics pipeline that tracks attraction availability across four
Walt Disney World parks, converts raw status snapshots into discrete downtime
events, and surfaces the results as an operations dashboard.

**Stack:** Python · DuckDB · dbt · GitHub Actions · Streamlit · Plotly

---

## The problem this solves

Ride status data arrives as point-in-time snapshots: *"Space Mountain, closed,
2:15pm."* That format answers almost nothing an operations leader actually asks.

- Counting closed snapshots triple-counts a single long outage.
- Raw downtime minutes treat a walk-on attraction and a 90-minute-wait headliner
  as equally important, which no operator believes.
- Availability averaged across a whole park hides the two assets causing most of
  the pain.

This pipeline restructures the data so the real questions become answerable:
**how often does each asset fail, how long is it down, and how many guests does
that actually affect?**

---

## Architecture

```
Queue-Times API
      │
      ▼
  src/ingest.py ──────► data/raw/snapshots_YYYY-MM-DD.parquet
      │                 (date-partitioned, append-only, deduped)
      ▼
  src/load.py ────────► DuckDB  raw.wait_time_snapshots
      │
      ▼
  dbt ────────────────► staging → marts
      │                 stg_snapshots
      │                 fct_downtime_events
      │                 dim_rides
      │                 agg_ride_daily
      │                 agg_park_daily
      ▼
  app/streamlit_app.py ─► dashboard
```

Ingestion runs on a **GitHub Actions schedule** every 30 minutes during park
operating hours, commits the new parquet partition back to the repository, and
rebuilds the models. No manual refresh, no local machine required.

### Why this shape

**Parquet as the source of truth, DuckDB as a build artifact.** The warehouse is
`.gitignore`d and fully reproducible from the parquet partitions with one
command. Raw history is never destroyed by a modeling mistake.

**All business logic in dbt, none in the loader.** `src/load.py` deliberately
does no interpretation — it stacks parquet into a table and stops. Every
business rule lives in version-controlled, tested SQL that a reviewer can read.

**Thresholds as dbt vars, not magic numbers.** Minimum outage duration, operating
hours, and snapshot interval are declared in `dbt_project.yml`, so changing what
counts as a breakdown is a one-line config change.

---

## The core transformation

`fct_downtime_events` is where the real work happens. It uses a
**gap-and-islands** pattern to collapse consecutive closed snapshots into single
discrete outage events:

```sql
-- flag each status flip
case when lag(is_open) over (partition by ride_id order by captured_at) = is_open
     then 0 else 1 end as is_status_change

-- running sum turns each uninterrupted run into a shared group id
sum(is_status_change) over (
    partition by ride_id order by captured_at
    rows between unbounded preceding and current row
) as status_group
```

Grouping on `status_group` turns *"closed at 2:15, 2:30, 2:45"* into one
45-minute event. That single change separates **failure frequency** from
**failure duration** — two different operational problems that raw snapshot
counts conflate.

Outages shorter than the configured minimum are dropped as sensor noise rather
than counted as breakdowns.

---

## The metric that matters: guest impact

Uptime percentage alone misranks maintenance priorities. `dim_rides` computes:

```
guest_impact_score = total_downtime_minutes × avg_wait_when_open ÷ 60
```

This weights lost availability by observed demand. An hour of downtime on an
attraction with a 90-minute standby line affects several times more guests than
an hour on a walk-on — so the ranking changes materially depending on which
metric you sort by. Sorting by raw uptime and sorting by guest impact produce
visibly different priority lists, which is exactly the point.

The same logic transfers directly to any fleet or asset-availability context:
weight downtime by utilisation, not by clock time.

---

## Data quality

16 dbt tests run on every scheduled build — uniqueness on event surrogate keys,
not-null constraints across the snapshot grain, and an `accepted_values` test on
the severity banding. A failed test fails the workflow rather than silently
publishing bad numbers to the dashboard.

---

## Running it locally

```bash
git clone https://github.com/youssefattafi/theme-park-uptime-analytics.git
cd theme-park-uptime-analytics

python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

make seed        # generate 60 days of synthetic history (no API key needed)
make build       # load → dbt run → dbt test
make dashboard   # open the Streamlit app
```

To pull live data instead of seeded data:

```bash
make ingest      # capture one real snapshot from the Queue-Times API
make build
```

---

## A note on the seeded data

Because this pipeline only accumulates real history once the scheduled job has
been running for weeks, `scripts/seed_demo_data.py` generates a **synthetic**
60-day history so the models and dashboard are testable and demonstrable
immediately.

The generator models a realistic demand curve, per-ride failure rates, and
persistent multi-snapshot outage runs — but **it is simulated data, not observed
data**. Three safeguards keep that fact from ever getting lost:

1. **Separate directories.** Seed data writes to `data/seed/`, live API
   snapshots to `data/raw/`. They cannot collide on a shared partition file.
2. **A `data_source` column** is stamped on every row at ingestion and carried
   through the staging layer, so provenance is queryable at any point in the
   warehouse.
3. **Live wins by default.** If both sources are present, `src/load.py` loads
   live data only and logs a warning. Combining them requires an explicit
   `--include-seed` flag.

Seed parquet is `.gitignore`d and regenerates deterministically (fixed random
seed), so the repository never ships simulated numbers as if they were observed.

---

## Repository layout

```
src/ingest.py              Queue-Times API client, retries, partitioned writes
src/load.py                Parquet → DuckDB raw layer
src/config.py              Paths, API config, business rule thresholds
scripts/seed_demo_data.py  Synthetic history generator
dbt/models/staging/        Cleaning, business-hours filtering, provenance passthrough
dbt/models/marts/          Downtime events, ride dimension, daily aggregates
app/streamlit_app.py       Executive dashboard
.github/workflows/         Scheduled ingestion + model rebuild
```

---

## Data source

Ride status is provided by the [Queue-Times](https://queue-times.com) community
API. This project is unaffiliated with and not endorsed by The Walt Disney
Company or Queue-Times.
