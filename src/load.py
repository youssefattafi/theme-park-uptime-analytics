"""Load raw parquet snapshots into the DuckDB warehouse.

Deliberately dumb: this layer does no business logic. It reads parquet
partitions, stacks them into `raw.wait_time_snapshots`, and stops. All
interpretation happens in dbt so the transformation logic is versioned,
testable, and reviewable as SQL.

Provenance rule: live API snapshots (data/raw) and synthetic seed data
(data/seed) live in separate directories and carry a `data_source` column.
If both are present, live data wins and the seed is ignored - simulated rows
must never quietly inflate a real number. Use --include-seed to override.
"""

from __future__ import annotations

import argparse
import logging
import sys

import duckdb

from src import config

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-7s | %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("load")

SELECT_TEMPLATE = """
    SELECT
        CAST(captured_at AS TIMESTAMP)        AS captured_at,
        CAST(park_id AS INTEGER)              AS park_id,
        CAST(park_name AS VARCHAR)            AS park_name,
        CAST(land_name AS VARCHAR)            AS land_name,
        CAST(ride_id AS INTEGER)              AS ride_id,
        CAST(ride_name AS VARCHAR)            AS ride_name,
        CAST(is_open AS BOOLEAN)              AS is_open,
        CAST(wait_time_minutes AS INTEGER)    AS wait_time_minutes,
        CAST(source_last_updated AS VARCHAR)  AS source_last_updated,
        CAST(data_source AS VARCHAR)          AS data_source
    FROM read_parquet(?)
"""


def run(include_seed: bool = False) -> int:
    live = sorted(config.RAW_DIR.glob("snapshots_*.parquet"))
    seed = sorted(config.SEED_DIR.glob("snapshots_*.parquet"))

    if live and seed and not include_seed:
        log.warning(
            "found %s live and %s seed partition(s) - loading LIVE only. "
            "Pass --include-seed to combine them.",
            len(live),
            len(seed),
        )
        seed = []

    if not live and not seed:
        log.error(
            "no parquet partitions found.\n"
            "  demo dataset : python scripts/seed_demo_data.py --days 60\n"
            "  live data    : python -m src.ingest"
        )
        return 0

    sources: list[tuple[str, str]] = []
    if live:
        sources.append(("live", str(config.RAW_DIR / "snapshots_*.parquet")))
    if seed:
        sources.append(("seed", str(config.SEED_DIR / "snapshots_*.parquet")))

    config.WAREHOUSE_PATH.parent.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect(str(config.WAREHOUSE_PATH))

    try:
        con.execute("CREATE SCHEMA IF NOT EXISTS raw;")
        con.execute("DROP TABLE IF EXISTS raw.wait_time_snapshots;")

        for index, (label, glob) in enumerate(sources):
            verb = (
                "CREATE TABLE raw.wait_time_snapshots AS"
                if index == 0
                else "INSERT INTO raw.wait_time_snapshots"
            )
            con.execute(f"{verb} {SELECT_TEMPLATE};", [glob])
            log.info("loaded %s partitions (%s)", label, len(live if label == "live" else seed))

        row_count = con.execute("SELECT COUNT(*) FROM raw.wait_time_snapshots;").fetchone()[0]
        span = con.execute(
            "SELECT MIN(captured_at), MAX(captured_at) FROM raw.wait_time_snapshots;"
        ).fetchone()
        breakdown = con.execute(
            "SELECT data_source, COUNT(*) FROM raw.wait_time_snapshots GROUP BY 1 ORDER BY 2 DESC;"
        ).fetchall()

        log.info("loaded %s rows into raw.wait_time_snapshots", f"{row_count:,}")
        log.info("coverage: %s -> %s", span[0], span[1])
        for source_name, count in breakdown:
            log.info("  data_source=%-18s %s rows", source_name, f"{count:,}")

        return row_count
    finally:
        con.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Load parquet snapshots into DuckDB.")
    parser.add_argument(
        "--include-seed",
        action="store_true",
        help="load synthetic seed data alongside live data (not recommended)",
    )
    args = parser.parse_args()

    if run(include_seed=args.include_seed) == 0:
        sys.exit(1)
