"""Pull a point-in-time snapshot of ride status from the Queue-Times API.

Each run captures the current open/closed state and posted wait time for every
tracked ride, then appends it to a date-partitioned parquet file. Running this
on a schedule builds the time series that everything downstream depends on.
"""

from __future__ import annotations

import argparse
import logging
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
import requests

from src import config

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-7s | %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("ingest")


def _get(url: str) -> dict | list:
    """GET with retries and backoff. Raises on final failure."""
    headers = {"User-Agent": config.USER_AGENT, "Accept": "application/json"}
    last_error: Exception | None = None

    for attempt in range(1, config.MAX_RETRIES + 1):
        try:
            response = requests.get(
                url, headers=headers, timeout=config.REQUEST_TIMEOUT_SECONDS
            )
            response.raise_for_status()
            return response.json()
        except Exception as exc:  # noqa: BLE001 - we retry on anything transient
            last_error = exc
            log.warning("attempt %s/%s failed for %s: %s", attempt, config.MAX_RETRIES, url, exc)
            if attempt < config.MAX_RETRIES:
                time.sleep(config.RETRY_BACKOFF_SECONDS * attempt)

    raise RuntimeError(f"exhausted retries for {url}") from last_error


def resolve_park_ids(target_names: list[str]) -> dict[str, int]:
    """Map park display names to their upstream IDs.

    The parks endpoint returns a list of park *groups* (operators), each with a
    nested list of parks. We flatten it and match on name.
    """
    payload = _get(config.PARKS_ENDPOINT)

    catalog: dict[str, int] = {}
    for group in payload:
        for park in group.get("parks", []):
            catalog[park["name"].strip()] = park["id"]

    resolved: dict[str, int] = {}
    for name in target_names:
        if name in catalog:
            resolved[name] = catalog[name]
        else:
            log.warning("park not found upstream, skipping: %r", name)

    if not resolved:
        raise RuntimeError("no target parks could be resolved from the API")

    log.info("resolved %s of %s target parks", len(resolved), len(target_names))
    return resolved


def _flatten_rides(payload: dict) -> list[dict]:
    """Queue-Times splits rides between top-level `rides` and nested `lands`."""
    rides: list[dict] = []

    for ride in payload.get("rides", []):
        rides.append({**ride, "land_name": "Uncategorized"})

    for land in payload.get("lands", []):
        land_name = land.get("name", "Uncategorized")
        for ride in land.get("rides", []):
            rides.append({**ride, "land_name": land_name})

    return rides


def fetch_park_snapshot(park_name: str, park_id: int, captured_at: datetime) -> pd.DataFrame:
    """Fetch current ride status for one park."""
    payload = _get(config.QUEUE_TIMES_ENDPOINT.format(park_id=park_id))
    rides = _flatten_rides(payload)

    if not rides:
        log.warning("no rides returned for %s (id=%s)", park_name, park_id)
        return pd.DataFrame()

    frame = pd.DataFrame(
        [
            {
                "captured_at": captured_at,
                "park_id": park_id,
                "park_name": park_name,
                "land_name": ride.get("land_name", "Uncategorized"),
                "ride_id": ride.get("id"),
                "ride_name": ride.get("name"),
                "is_open": bool(ride.get("is_open", False)),
                "wait_time_minutes": ride.get("wait_time"),
                "source_last_updated": ride.get("last_updated"),
                "data_source": "queue-times-api",
            }
            for ride in rides
        ]
    )

    log.info("%-32s %3d rides | %3d open", park_name, len(frame), int(frame["is_open"].sum()))
    return frame


def run() -> Path | None:
    """Capture one snapshot across all target parks and append to parquet."""
    captured_at = datetime.now(timezone.utc).replace(microsecond=0)
    park_ids = resolve_park_ids(config.TARGET_PARKS)

    frames = []
    for park_name, park_id in park_ids.items():
        try:
            frames.append(fetch_park_snapshot(park_name, park_id, captured_at))
        except Exception as exc:  # noqa: BLE001
            # One bad park should never kill the whole scheduled run.
            log.error("failed to ingest %s: %s", park_name, exc)

    frames = [f for f in frames if not f.empty]
    if not frames:
        log.error("snapshot produced no rows; nothing written")
        return None

    snapshot = pd.concat(frames, ignore_index=True)
    snapshot["wait_time_minutes"] = pd.to_numeric(
        snapshot["wait_time_minutes"], errors="coerce"
    )

    config.RAW_DIR.mkdir(parents=True, exist_ok=True)
    partition = config.RAW_DIR / f"snapshots_{captured_at:%Y-%m-%d}.parquet"

    if partition.exists():
        existing = pd.read_parquet(partition)
        snapshot = pd.concat([existing, snapshot], ignore_index=True)
        snapshot = snapshot.drop_duplicates(subset=["captured_at", "ride_id"], keep="last")

    snapshot.to_parquet(partition, index=False)
    log.info("wrote %s rows -> %s", len(snapshot), partition.name)
    return partition


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Ingest one Queue-Times snapshot.")
    parser.parse_args()

    try:
        run()
    except Exception as exc:  # noqa: BLE001
        log.critical("ingestion failed: %s", exc)
        sys.exit(1)
