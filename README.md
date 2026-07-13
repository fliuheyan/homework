# Homework ETL + Grafana Monitoring

## Overview

This repository builds a small ETL pipeline on top of a single PostgreSQL database:

- `raw` schema stores ingested source data as text
- `core` schema stores cleaned business tables
- `audit` schema stores ETL run logs and Grafana-facing quality metrics

Grafana is provisioned as the frontend and reads metrics directly from the existing PostgreSQL instance.

## Prerequisites

- Docker and Docker Compose
- Python 3.x (for local test execution)

## Run the stack

From the repository root:

```bash
docker compose up --build
```

This starts:

- `postgres` on `localhost:5432`
- `etl`, which runs:
  1. `python -m etl.ingest_raw`
  2. `python -m etl.data_check`
  3. `python -m etl.main`
- `grafana` on `http://localhost:3000`

Grafana default credentials:

- username: `${GRAFANA_ADMIN_USER:-admin}`
- password: `${GRAFANA_ADMIN_PASSWORD:-admin}`

## Grafana setup

Provisioning files live under:

- `./grafana/provisioning/datasources/postgres.yaml`
- `./grafana/provisioning/dashboards/dashboard.yaml`
- `./grafana/dashboards/etl-monitoring.json`

Grafana uses the existing PostgreSQL container as its datasource:

- host: `${DB_HOST:-postgres}:${DB_PORT:-5432}`
- database: `${DB_NAME:-bi_db}`
- user: `${DB_USER:-bi_user}`

## Metrics exposed for Grafana

The ETL persists monitoring data into PostgreSQL and exposes Grafana-friendly views:

- `audit.etl_run_log`: ETL batch execution records
- `audit.data_quality_issue_summary`: field-level quality issue counts by batch
- `audit.data_quality_table_summary`: table-level quality totals by batch
- `audit.v_etl_run_metrics`: runtime/status metrics per ETL run
- `audit.v_etl_health_kpis`: latest status, success rate, average runtime, P95 runtime
- `audit.v_data_quality_batch_metrics`: total quality issues and issue rates by batch/table
- `audit.v_data_quality_issue_metrics`: field-level issue metrics over time
- `audit.v_latest_data_quality_summary`: latest batch quality snapshot
- `audit.v_table_volume`: current row counts for raw/core tables
- `audit.v_table_freshness`: latest timestamps and freshness lag for raw/core tables

## Dashboard contents

The provisioned dashboard `Homework ETL Monitoring` focuses on:

- latest ETL status
- job success rate
- average runtime
- latest batch quality issue count
- ETL runtime trend
- quality issue trend
- per-table freshness
- current raw/core table volumes

## Data quality report

`python -m etl.data_check` still writes the Markdown report to:

- `/app/reports/data_quality_report.md`

It now also writes the same quality results into PostgreSQL for Grafana queries.

## Tests

From the repository root:

```bash
python -m pytest -q
```
