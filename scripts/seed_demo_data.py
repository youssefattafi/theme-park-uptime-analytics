"""Generate a realistic synthetic snapshot history.

Why this exists: the pipeline only accumulates real history once the scheduled
job has been running for weeks. This script fabricates a comparable history so
the models and dashboard can be developed, tested, and demoed on day one.

The generator models three things that make the output behave like real data:
  1. Demand curve   - waits build through the morning, peak midday, taper at close.
  2. Reliability    - each ride has its own failure rate; some are chronically bad.
  3. Downtime runs  - outages persist across consecutive snapshots, not one-offs.

Usage:  python scripts/seed_demo_data.py --days 60
"""

from __future__ import annotations

import argparse
import math
import random
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from src import config  # noqa: E402

RNG_SEED = 42

# ride_name, land, popularity (0-1), hourly failure probability, mean repair minutes
RIDE_CATALOG: dict[str, list[tuple[str, str, float, float, int]]] = {
    "Magic Kingdom": [
        ("Seven Dwarfs Mine Train", "Fantasyland", 0.98, 0.055, 35),
        ("Space Mountain", "Tomorrowland", 0.92, 0.070, 45),
        ("TRON Lightcycle / Run", "Tomorrowland", 0.95, 0.085, 50),
        ("Peter Pan's Flight", "Fantasyland", 0.90, 0.030, 25),
        ("Big Thunder Mountain Railroad", "Frontierland", 0.85, 0.045, 40),
        ("Haunted Mansion", "Liberty Square", 0.80, 0.025, 30),
        ("Jungle Cruise", "Adventureland", 0.72, 0.030, 25),
        ("Pirates of the Caribbean", "Adventureland", 0.70, 0.022, 30),
        ("It's a Small World", "Fantasyland", 0.55, 0.018, 20),
        ("Buzz Lightyear's Space Ranger Spin", "Tomorrowland", 0.50, 0.028, 20),
    ],
    "Epcot": [
        ("Guardians of the Galaxy: Cosmic Rewind", "World Discovery", 0.99, 0.090, 55),
        ("Frozen Ever After", "World Showcase", 0.88, 0.040, 35),
        ("Remy's Ratatouille Adventure", "World Showcase", 0.82, 0.035, 30),
        ("Test Track", "World Discovery", 0.90, 0.110, 60),
        ("Soarin' Around the World", "World Nature", 0.75, 0.030, 30),
        ("Spaceship Earth", "World Celebration", 0.60, 0.020, 25),
        ("Mission: SPACE", "World Discovery", 0.45, 0.030, 30),
        ("Living with the Land", "World Nature", 0.35, 0.015, 20),
    ],
    "Disney's Hollywood Studios": [
        ("Rise of the Resistance", "Galaxy's Edge", 0.99, 0.120, 65),
        ("Slinky Dog Dash", "Toy Story Land", 0.94, 0.045, 35),
        ("Millennium Falcon: Smugglers Run", "Galaxy's Edge", 0.85, 0.055, 40),
        ("Tower of Terror", "Sunset Boulevard", 0.86, 0.050, 40),
        ("Rock 'n' Roller Coaster", "Sunset Boulevard", 0.84, 0.075, 50),
        ("Mickey & Minnie's Runaway Railway", "Hollywood Boulevard", 0.78, 0.035, 30),
        ("Toy Story Mania!", "Toy Story Land", 0.70, 0.030, 25),
        ("Star Tours", "Echo Lake", 0.48, 0.025, 25),
    ],
    "Disney's Animal Kingdom": [
        ("Avatar Flight of Passage", "Pandora", 0.99, 0.065, 50),
        ("Na'vi River Journey", "Pandora", 0.80, 0.030, 30),
        ("Expedition Everest", "Asia", 0.82, 0.060, 45),
        ("Kilimanjaro Safaris", "Africa", 0.75, 0.025, 30),
        ("DINOSAUR", "DinoLand U.S.A.", 0.55, 0.040, 30),
        ("Kali River Rapids", "Asia", 0.50, 0.045, 35),
        ("TriceraTop Spin", "DinoLand U.S.A.", 0.25, 0.015, 20),
    ],
}

PARK_IDS = {
    "Magic Kingdom": 6,
    "Epcot": 5,
    "Disney's Hollywood Studios": 7,
    "Disney's Animal Kingdom": 8,
}


def demand_multiplier(hour: int, minute: int) -> float:
    """Bell-ish curve peaking early afternoon."""
    t = hour + minute / 60.0
    peak, spread = 13.5, 3.4
    return math.exp(-((t - peak) ** 2) / (2 * spread**2))


def weekday_multiplier(day: datetime) -> float:
    """Weekends and Fridays are busier."""
    return {0: 0.82, 1: 0.78, 2: 0.80, 3: 0.86, 4: 1.05, 5: 1.25, 6: 1.15}[day.weekday()]


def seasonal_multiplier(day: datetime) -> float:
    """Summer and holiday peaks."""
    doy = day.timetuple().tm_yday
    return 1.0 + 0.22 * math.sin((doy - 80) / 365 * 2 * math.pi)


def generate(days: int) -> pd.DataFrame:
    random.seed(RNG_SEED)

    end = datetime.now(timezone.utc).replace(
        hour=0, minute=0, second=0, microsecond=0, tzinfo=None
    )
    start = end - timedelta(days=days)

    step = timedelta(minutes=config.SNAPSHOT_INTERVAL_MINUTES)
    rows: list[dict] = []

    # Tracks how many more minutes each ride stays down.
    downtime_remaining: dict[int, int] = {}

    ride_registry: list[tuple[int, str, int, str, str, float, float, int]] = []
    ride_id = 1000
    for park_name, rides in RIDE_CATALOG.items():
        for ride_name, land, popularity, failure_rate, repair_mean in rides:
            ride_id += 1
            ride_registry.append(
                (
                    ride_id,
                    park_name,
                    PARK_IDS[park_name],
                    land,
                    ride_name,
                    popularity,
                    failure_rate,
                    repair_mean,
                )
            )

    current = start
    while current < end:
        hour = current.hour

        # Only emit snapshots during operating hours.
        if not (config.PARK_OPEN_HOUR <= hour < config.PARK_CLOSE_HOUR):
            current += step
            continue

        demand = (
            demand_multiplier(hour, current.minute)
            * weekday_multiplier(current)
            * seasonal_multiplier(current)
        )

        for (
            rid,
            park_name,
            park_id,
            land,
            ride_name,
            popularity,
            failure_rate,
            repair_mean,
        ) in ride_registry:
            remaining = downtime_remaining.get(rid, 0)

            if remaining > 0:
                is_open = False
                downtime_remaining[rid] = remaining - config.SNAPSHOT_INTERVAL_MINUTES
            else:
                # Convert hourly failure rate to per-snapshot probability.
                per_snapshot = failure_rate * (config.SNAPSHOT_INTERVAL_MINUTES / 60.0)
                if random.random() < per_snapshot:
                    is_open = False
                    duration = max(
                        config.MIN_DOWNTIME_MINUTES,
                        int(random.expovariate(1 / repair_mean)),
                    )
                    downtime_remaining[rid] = duration - config.SNAPSHOT_INTERVAL_MINUTES
                else:
                    is_open = True

            if is_open:
                base = 95 * popularity
                noise = random.uniform(0.75, 1.3)
                wait = int(max(0, round(base * demand * noise / 5) * 5))
            else:
                wait = 0

            rows.append(
                {
                    "captured_at": current,
                    "park_id": park_id,
                    "park_name": park_name,
                    "land_name": land,
                    "ride_id": rid,
                    "ride_name": ride_name,
                    "is_open": is_open,
                    "wait_time_minutes": wait,
                    "source_last_updated": current.isoformat(),
                }
            )

        current += step

    return pd.DataFrame(rows)


def main() -> None:
    parser = argparse.ArgumentParser(description="Seed synthetic snapshot history.")
    parser.add_argument("--days", type=int, default=60, help="days of history (default 60)")
    args = parser.parse_args()

    print(f"Generating {args.days} days of synthetic snapshots...")
    frame = generate(args.days)

    config.RAW_DIR.mkdir(parents=True, exist_ok=True)
    for existing in config.RAW_DIR.glob("snapshots_*.parquet"):
        existing.unlink()

    written = 0
    for day, group in frame.groupby(frame["captured_at"].dt.date):
        path = config.RAW_DIR / f"snapshots_{day:%Y-%m-%d}.parquet"
        group.to_parquet(path, index=False)
        written += 1

    open_rate = frame["is_open"].mean()
    print(f"  rows written : {len(frame):,}")
    print(f"  partitions   : {written}")
    print(f"  rides        : {frame['ride_id'].nunique()}")
    print(f"  overall uptime: {open_rate:.1%}")
    print(f"  -> {config.RAW_DIR}")


if __name__ == "__main__":
    main()
