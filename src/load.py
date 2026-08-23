"""Load raw parquet snapshots into the DuckDB warehouse.

Deliberately dumb: this layer does no business logic. It reads every parquet
partition, stacks them into `raw.wait_time_snapshots`, and stops. All
interpretation happens in dbt so the transformation logic is versioned,
testable, and reviewable as SQL.
"""

from __future__ import annotations

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


def run() -> int:
    partitions = sorted(config.RAW_DIR.glob("snapshots_*.parquet"))
    if not partitions:
        log.error(
            "no parquet partitions found in %s. "
            "Run `python -m src.ingest` or `python scripts/seed_demo_data.py` first.",
            config.RAW_DIR,
        )
        return 0

    log.info("found %s partition(s)", len(partitions))
    config.WAREHOUSE_PATH.parent.mkdir(parents=True, exist_ok=True)

    con = duckdb.connect(str(config.WAREHOUSE_PATH))
    try:
        con.execute("CREATE SCHEMA IF NOT EXISTS raw;")
        con.execute("DROP TABLE IF EXISTS raw.wait_time_snapshots;")
        con.execute(
            """
            CREATE TABLE raw.wait_time_snapshots AS
            SELECT
                CAST(captured_at AS TIMESTAMP)        AS captured_at,
                CAST(park_id AS INTEGER)              AS park_id,
                CAST(park_name AS VARCHAR)            AS park_name,
                CAST(land_name AS VARCHAR)            AS land_name,
                CAST(ride_id AS INTEGER)              AS ride_id,
                CAST(ride_name AS VARCHAR)            AS ride_name,
                CAST(is_open AS BOOLEAN)              AS is_open,
                CAST(wait_time_minutes AS INTEGER)    AS wait_time_minutes,
                CAST(source_last_updated AS VARCHAR)  AS source_last_updated
            FROM read_parquet($glob);
            """,
            {"glob": str(config.RAW_DIR / "snapshots_*.parquet")},
        )

        row_count = con.execute("SELECT COUNT(*) FROM raw.wait_time_snapshots;").fetchone()[0]
        span = con.execute(
            "SELECT MIN(captured_at), MAX(captured_at) FROM raw.wait_time_snapshots;"
        ).fetchone()

        log.info("loaded %s rows into raw.wait_time_snapshots", f"{row_count:,}")
        log.info("coverage: %s -> %s", span[0], span[1])
        return row_count
    finally:
        con.close()


if __name__ == "__main__":
    if run() == 0:
        sys.exit(1)
