.PHONY: help install seed ingest load models test build dashboard clean

help:
	@echo "seed       Generate 60 days of synthetic snapshots (no API needed)"
	@echo "ingest     Capture one live snapshot from the Queue-Times API"
	@echo "load       Load parquet snapshots into DuckDB"
	@echo "models     Run dbt transformations"
	@echo "test       Run dbt data quality tests"
	@echo "build      load + models + test"
	@echo "dashboard  Launch the Streamlit dashboard"

install:
	pip install -r requirements.txt

seed:
	python scripts/seed_demo_data.py --days 60

ingest:
	python -m src.ingest

load:
	python -m src.load

models:
	cd dbt && dbt run

test:
	cd dbt && dbt test

build: load models test

dashboard:
	streamlit run app/streamlit_app.py

clean:
	rm -f data/warehouse.duckdb
	rm -rf dbt/target dbt/logs
