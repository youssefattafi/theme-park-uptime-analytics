"""Central configuration for the theme park uptime analytics pipeline."""

from pathlib import Path

# ---------------------------------------------------------------- paths
PROJECT_ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = PROJECT_ROOT / "data"
RAW_DIR = DATA_DIR / "raw"
WAREHOUSE_PATH = DATA_DIR / "warehouse.duckdb"

# ---------------------------------------------------------------- api
API_BASE = "https://queue-times.com"
PARKS_ENDPOINT = f"{API_BASE}/parks.json"
QUEUE_TIMES_ENDPOINT = f"{API_BASE}/parks/{{park_id}}/queue_times.json"

REQUEST_TIMEOUT_SECONDS = 20
MAX_RETRIES = 3
RETRY_BACKOFF_SECONDS = 3

# Be a good API citizen: Queue-Times is a free community API.
USER_AGENT = (
    "theme-park-uptime-analytics/1.0 "
    "(portfolio project; https://github.com/youssefattafi)"
)

# ---------------------------------------------------------------- scope
# Parks are resolved by name at runtime rather than hardcoded by ID, so the
# pipeline keeps working if the upstream API renumbers its parks.
TARGET_PARKS = [
    "Magic Kingdom",
    "Epcot",
    "Disney's Hollywood Studios",
    "Disney's Animal Kingdom",
]

# ---------------------------------------------------------------- business rules
# A ride must be reported closed for at least this long before we count it as a
# genuine downtime event. Filters out single-snapshot blips and refurb noise.
MIN_DOWNTIME_MINUTES = 15

# Snapshots outside these hours are excluded from uptime math: a ride being
# "closed" at 3am is not a breakdown.
PARK_OPEN_HOUR = 9
PARK_CLOSE_HOUR = 22

# Expected gap between snapshots. Used to convert snapshot counts into minutes.
SNAPSHOT_INTERVAL_MINUTES = 15
