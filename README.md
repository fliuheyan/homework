# Homework ETL

## Description

This repository builds a small ETL pipeline on top of a single PostgreSQL database:

- `raw` schema stores ingested source data as text
- `core` schema stores cleaned business tables
- `audit` schema stores ETL run logs and Grafana-facing quality metrics

Grafana is provisioned as the frontend and reads metrics directly from the existing PostgreSQL instance.

## Prerequisites

- Docker and Docker Compose
- Python 3.x (for local test execution)

## How to run this project

From the repository root:

```bash
./auto/run.sh
```

### Generate data quality report

```bash
./auto/generate_check_report.sh
```

The report will be generated at reports/data_quality_report.md.

## Monthly customer order totals

After each `core.*` rebuild in `python -m etl.main`, the ETL refreshes the rows for the current batch in `audit.customer_monthly_order_summary`.

Example query:

```sql
SELECT batch_id, customer_id, customer_email, order_month, total_net_amount
FROM audit.customer_monthly_order_summary
ORDER BY batch_id DESC, customer_id, order_month;
```

## How to connect to the database

```bash
docker compose exec -e PGPASSWORD=bi_pass postgres psql -U bi_user -d bi_db
```

## Tests

From the repository root:

```bash
python -m pytest -q
```
