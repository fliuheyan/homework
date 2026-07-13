CREATE SCHEMA IF NOT EXISTS raw;
CREATE SCHEMA IF NOT EXISTS core;
CREATE SCHEMA IF NOT EXISTS audit;

-- =========================
-- RAW
-- =========================
CREATE TABLE IF NOT EXISTS raw.orders (
  raw_id BIGSERIAL PRIMARY KEY,
  order_date_text TEXT,
  email_text TEXT,
  net_amount_text TEXT,
  order_number_text TEXT,
  source_file TEXT NOT NULL,
  sheet_name TEXT NOT NULL,
  load_time TIMESTAMP NOT NULL DEFAULT NOW(),
  batch_id TEXT NOT NULL,
  row_num_in_sheet INT,
  ingested_at TIMESTAMP NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS raw.customer (
  raw_id BIGSERIAL PRIMARY KEY,
  email_text TEXT,
  birthday_text TEXT,
  gender_text TEXT,
  country_text TEXT,
  zip_code_text TEXT,
  city_text TEXT,
  loyalty_score_text TEXT,
  source_file TEXT NOT NULL,
  sheet_name TEXT NOT NULL,
  load_time TIMESTAMP NOT NULL DEFAULT NOW(),
  batch_id TEXT NOT NULL,
  row_num_in_sheet INT,
  ingested_at TIMESTAMP NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS raw.survey (
  raw_id BIGSERIAL PRIMARY KEY,
  respondent_key_text TEXT,
  diet_pref_text TEXT,
  taste_pref_text TEXT,
  source_file TEXT NOT NULL,
  sheet_name TEXT NOT NULL,
  load_time TIMESTAMP NOT NULL DEFAULT NOW(),
  batch_id TEXT NOT NULL,
  row_num_in_sheet INT,
  ingested_at TIMESTAMP NOT NULL DEFAULT NOW()
);

-- =========================
-- CORE
-- =========================
CREATE TABLE IF NOT EXISTS core.customer (
  customer_id BIGSERIAL PRIMARY KEY,
  email TEXT,
  email_norm TEXT,
  birthday DATE,
  gender TEXT,
  country_code CHAR(2),
  zip_code TEXT,
  city TEXT,
  loyalty_score SMALLINT,
  created_at TIMESTAMP NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS core.orders (
  order_id BIGSERIAL PRIMARY KEY,
  order_number TEXT UNIQUE,
  customer_id BIGINT NOT NULL,
  order_date DATE NOT NULL,
  net_amount NUMERIC(12,2) NOT NULL,
  currency_code CHAR(3) NOT NULL DEFAULT 'EUR',
  created_at TIMESTAMP NOT NULL DEFAULT NOW(),
  CONSTRAINT fk_orders_customer
    FOREIGN KEY (customer_id)
    REFERENCES core.customer(customer_id)
);

CREATE TABLE IF NOT EXISTS core.survey (
  survey_id BIGSERIAL PRIMARY KEY,
  respondent_key TEXT,
  key_type TEXT, -- email / order_number / invalid
  customer_id BIGINT NULL,
  order_id BIGINT NULL,
  diet_pref TEXT,
  taste_pref TEXT,
  created_at TIMESTAMP NOT NULL DEFAULT NOW(),
  CONSTRAINT fk_survey_customer
    FOREIGN KEY (customer_id)
    REFERENCES core.customer(customer_id),
  CONSTRAINT fk_survey_order
    FOREIGN KEY (order_id)
    REFERENCES core.orders(order_id)
);

-- =========================
-- AUDIT
-- =========================
CREATE TABLE IF NOT EXISTS audit.etl_run_log (
  run_id BIGSERIAL PRIMARY KEY,
  batch_id TEXT,
  started_at TIMESTAMP,
  ended_at TIMESTAMP,
  status TEXT,
  rows_orders INT,
  rows_customer INT,
  rows_survey INT,
  rows_orders_core INT,
  rows_customer_core INT,
  rows_survey_core INT,
  error_message TEXT
);

ALTER TABLE audit.etl_run_log
  ADD COLUMN IF NOT EXISTS extract_duration_seconds DOUBLE PRECISION,
  ADD COLUMN IF NOT EXISTS transform_duration_seconds DOUBLE PRECISION,
  ADD COLUMN IF NOT EXISTS load_duration_seconds DOUBLE PRECISION;

CREATE TABLE IF NOT EXISTS audit.data_quality_issue_summary (
  batch_id TEXT NOT NULL,
  table_name TEXT NOT NULL,
  column_name TEXT NOT NULL,
  description TEXT NOT NULL,
  invalid_count INT NOT NULL,
  created_at TIMESTAMP NOT NULL DEFAULT NOW(),
  PRIMARY KEY (batch_id, table_name, column_name, description)
);

CREATE TABLE IF NOT EXISTS audit.data_quality_table_summary (
  batch_id TEXT NOT NULL,
  table_name TEXT NOT NULL,
  total_records INT NOT NULL,
  total_issues INT NOT NULL,
  created_at TIMESTAMP NOT NULL DEFAULT NOW(),
  PRIMARY KEY (batch_id, table_name)
);

CREATE TABLE IF NOT EXISTS audit.customer_monthly_order_summary (
  batch_id TEXT NOT NULL,
  customer_id BIGINT NOT NULL,
  customer_email TEXT,
  order_month DATE NOT NULL,
  order_count INT NOT NULL,
  total_net_amount NUMERIC(14,2) NOT NULL,
  refreshed_at TIMESTAMP NOT NULL DEFAULT NOW(),
  PRIMARY KEY (batch_id, customer_id, order_month)
);

CREATE OR REPLACE VIEW audit.v_etl_run_metrics AS
SELECT
  run_id,
  batch_id,
  started_at,
  ended_at,
  status,
  CASE
    WHEN ended_at IS NULL THEN NULL
    ELSE EXTRACT(EPOCH FROM (ended_at - started_at))::DOUBLE PRECISION
  END AS runtime_seconds,
  rows_orders,
  rows_customer,
  rows_survey,
  rows_orders_core,
  rows_customer_core,
  rows_survey_core,
  extract_duration_seconds,
  transform_duration_seconds,
  load_duration_seconds,
  error_message
FROM audit.etl_run_log
WHERE started_at IS NOT NULL;

CREATE OR REPLACE VIEW audit.v_etl_health_kpis AS
WITH latest_run AS (
  SELECT batch_id, status, started_at, ended_at
  FROM audit.etl_run_log
  ORDER BY started_at DESC NULLS LAST, run_id DESC
  LIMIT 1
),
latest_success AS (
  SELECT batch_id, ended_at
  FROM audit.etl_run_log
  WHERE status = 'success'
  ORDER BY ended_at DESC NULLS LAST, run_id DESC
  LIMIT 1
),
raw_batches AS (
  SELECT
    batch_id,
    MAX(ingested_at) AS latest_ingested_at
  FROM (
    SELECT batch_id, ingested_at FROM raw.orders
    UNION ALL
    SELECT batch_id, ingested_at FROM raw.customer
    UNION ALL
    SELECT batch_id, ingested_at FROM raw.survey
  ) raw_batch_events
  WHERE batch_id IS NOT NULL
  GROUP BY batch_id
),
backlog AS (
  SELECT COUNT(*)::INT AS backlog_size
  FROM raw_batches rb
  LEFT JOIN (
    SELECT DISTINCT batch_id
    FROM audit.etl_run_log
    WHERE status = 'success'
  ) completed
    ON completed.batch_id = rb.batch_id
  WHERE completed.batch_id IS NULL
),
aggregates AS (
  SELECT
    COUNT(*)::INT AS total_runs,
    COUNT(*) FILTER (WHERE status = 'success')::INT AS success_runs,
    COUNT(*) FILTER (WHERE status = 'failed')::INT AS failed_runs,
    COUNT(*) FILTER (WHERE status = 'running')::INT AS running_runs,
    ROUND(
      (100.0 * COUNT(*) FILTER (WHERE status = 'success') / NULLIF(COUNT(*), 0))::NUMERIC,
      2
    ) AS success_rate_pct,
    ROUND(
      (AVG(EXTRACT(EPOCH FROM (ended_at - started_at))) FILTER (WHERE ended_at IS NOT NULL))::NUMERIC,
      2
    ) AS avg_runtime_seconds,
    ROUND(
      (
        PERCENTILE_CONT(0.50) WITHIN GROUP (ORDER BY EXTRACT(EPOCH FROM (ended_at - started_at)))
        FILTER (WHERE ended_at IS NOT NULL)
      )::NUMERIC,
      2
    ) AS p50_runtime_seconds,
    ROUND(
      (
        PERCENTILE_CONT(0.95) WITHIN GROUP (ORDER BY EXTRACT(EPOCH FROM (ended_at - started_at)))
        FILTER (WHERE ended_at IS NOT NULL)
      )::NUMERIC,
      2
    ) AS p95_runtime_seconds
  FROM audit.etl_run_log
)
SELECT
  a.total_runs,
  a.success_runs,
  a.failed_runs,
  a.running_runs,
  a.success_rate_pct,
  a.avg_runtime_seconds,
  a.p50_runtime_seconds,
  a.p95_runtime_seconds,
  b.backlog_size,
  ls.ended_at AS last_successful_load_at,
  ls.batch_id AS last_successful_batch_id,
  lr.batch_id AS latest_batch_id,
  lr.status AS latest_status,
  lr.started_at AS latest_started_at,
  lr.ended_at AS latest_ended_at
FROM aggregates a
LEFT JOIN backlog b ON TRUE
LEFT JOIN latest_success ls ON TRUE
LEFT JOIN latest_run lr ON TRUE;

CREATE OR REPLACE VIEW audit.v_etl_step_duration_metrics AS
SELECT
  batch_id,
  started_at,
  ended_at,
  'extract'::TEXT AS step,
  extract_duration_seconds AS duration_seconds
FROM audit.v_etl_run_metrics
WHERE extract_duration_seconds IS NOT NULL
UNION ALL
SELECT
  batch_id,
  started_at,
  ended_at,
  'transform'::TEXT AS step,
  transform_duration_seconds AS duration_seconds
FROM audit.v_etl_run_metrics
WHERE transform_duration_seconds IS NOT NULL
UNION ALL
SELECT
  batch_id,
  started_at,
  ended_at,
  'load'::TEXT AS step,
  load_duration_seconds AS duration_seconds
FROM audit.v_etl_run_metrics
WHERE load_duration_seconds IS NOT NULL;

CREATE OR REPLACE VIEW audit.v_data_quality_batch_metrics AS
SELECT
  s.batch_id,
  r.started_at,
  r.ended_at,
  s.table_name,
  s.total_records,
  s.total_issues,
  ROUND((100.0 * s.total_issues / NULLIF(s.total_records, 0))::NUMERIC, 2) AS issue_rate_pct
FROM audit.data_quality_table_summary s
LEFT JOIN audit.etl_run_log r
  ON r.batch_id = s.batch_id;

CREATE OR REPLACE VIEW audit.v_data_quality_issue_metrics AS
SELECT
  i.batch_id,
  r.started_at,
  r.ended_at,
  i.created_at,
  i.table_name,
  i.column_name,
  i.description,
  i.invalid_count
FROM audit.data_quality_issue_summary i
LEFT JOIN audit.etl_run_log r
  ON r.batch_id = i.batch_id;

CREATE OR REPLACE VIEW audit.v_latest_data_quality_summary AS
WITH latest_batch AS (
  SELECT batch_id
  FROM audit.data_quality_table_summary
  ORDER BY created_at DESC, batch_id DESC
  LIMIT 1
)
SELECT
  s.batch_id,
  s.table_name,
  s.total_records,
  s.total_issues,
  ROUND((100.0 * s.total_issues / NULLIF(s.total_records, 0))::NUMERIC, 2) AS issue_rate_pct,
  s.created_at
FROM audit.data_quality_table_summary s
JOIN latest_batch lb
  ON lb.batch_id = s.batch_id;

CREATE OR REPLACE VIEW audit.v_data_quality_overview AS
SELECT
  batch_id,
  MAX(created_at) AS created_at,
  SUM(total_records)::INT AS total_records,
  SUM(total_issues)::INT AS total_issues,
  ROUND(
    (
      100.0 * SUM(total_issues) / NULLIF(SUM(total_records), 0)
    )::NUMERIC,
    2
  ) AS error_rate_pct
FROM audit.data_quality_table_summary
GROUP BY batch_id;

CREATE OR REPLACE VIEW audit.v_latest_data_quality_overview AS
WITH latest_batch AS (
  SELECT batch_id
  FROM audit.data_quality_table_summary
  ORDER BY created_at DESC, batch_id DESC
  LIMIT 1
)
SELECT
  o.batch_id,
  o.created_at,
  o.total_records,
  o.total_issues,
  o.error_rate_pct
FROM audit.v_data_quality_overview o
JOIN latest_batch lb
  ON lb.batch_id = o.batch_id;

CREATE OR REPLACE VIEW audit.v_table_volume AS
SELECT 'raw.orders'::TEXT AS table_name, COUNT(*)::BIGINT AS row_count FROM raw.orders
UNION ALL
SELECT 'raw.customer'::TEXT AS table_name, COUNT(*)::BIGINT AS row_count FROM raw.customer
UNION ALL
SELECT 'raw.survey'::TEXT AS table_name, COUNT(*)::BIGINT AS row_count FROM raw.survey
UNION ALL
SELECT 'core.orders'::TEXT AS table_name, COUNT(*)::BIGINT AS row_count FROM core.orders
UNION ALL
SELECT 'core.customer'::TEXT AS table_name, COUNT(*)::BIGINT AS row_count FROM core.customer
UNION ALL
SELECT 'core.survey'::TEXT AS table_name, COUNT(*)::BIGINT AS row_count FROM core.survey;

CREATE OR REPLACE VIEW audit.v_table_freshness AS
SELECT
  'raw.orders'::TEXT AS table_name,
  MAX(ingested_at) AS latest_timestamp,
  ROUND(EXTRACT(EPOCH FROM (NOW() - MAX(ingested_at)))::NUMERIC, 2) AS freshness_lag_seconds
FROM raw.orders
UNION ALL
SELECT
  'raw.customer'::TEXT AS table_name,
  MAX(ingested_at) AS latest_timestamp,
  ROUND(EXTRACT(EPOCH FROM (NOW() - MAX(ingested_at)))::NUMERIC, 2) AS freshness_lag_seconds
FROM raw.customer
UNION ALL
SELECT
  'raw.survey'::TEXT AS table_name,
  MAX(ingested_at) AS latest_timestamp,
  ROUND(EXTRACT(EPOCH FROM (NOW() - MAX(ingested_at)))::NUMERIC, 2) AS freshness_lag_seconds
FROM raw.survey
UNION ALL
SELECT
  'core.orders'::TEXT AS table_name,
  MAX(created_at) AS latest_timestamp,
  ROUND(EXTRACT(EPOCH FROM (NOW() - MAX(created_at)))::NUMERIC, 2) AS freshness_lag_seconds
FROM core.orders
UNION ALL
SELECT
  'core.customer'::TEXT AS table_name,
  MAX(created_at) AS latest_timestamp,
  ROUND(EXTRACT(EPOCH FROM (NOW() - MAX(created_at)))::NUMERIC, 2) AS freshness_lag_seconds
FROM core.customer
UNION ALL
SELECT
  'core.survey'::TEXT AS table_name,
  MAX(created_at) AS latest_timestamp,
  ROUND(EXTRACT(EPOCH FROM (NOW() - MAX(created_at)))::NUMERIC, 2) AS freshness_lag_seconds
FROM core.survey;

CREATE OR REPLACE VIEW audit.v_db_system_health AS
SELECT
  current_database()::TEXT AS database_name,
  d.numbackends::INT AS connections,
  d.deadlocks::BIGINT AS deadlocks,
  d.blks_read::BIGINT AS blocks_read,
  d.blks_hit::BIGINT AS blocks_hit,
  d.temp_bytes::BIGINT AS temp_bytes,
  pg_database_size(current_database())::BIGINT AS storage_bytes,
  pg_size_pretty(pg_database_size(current_database()))::TEXT AS storage_pretty,
  d.stats_reset
FROM pg_stat_database d
WHERE d.datname = current_database();

-- =========================
-- INDEXES
-- =========================
CREATE INDEX IF NOT EXISTS idx_customer_email_norm ON core.customer(email_norm);
CREATE INDEX IF NOT EXISTS idx_orders_customer_id ON core.orders(customer_id);
CREATE INDEX IF NOT EXISTS idx_orders_order_date ON core.orders(order_date);
CREATE INDEX IF NOT EXISTS idx_survey_customer_id ON core.survey(customer_id);
CREATE INDEX IF NOT EXISTS idx_survey_order_id ON core.survey(order_id);
CREATE INDEX IF NOT EXISTS idx_etl_run_log_batch_id ON audit.etl_run_log(batch_id);
CREATE INDEX IF NOT EXISTS idx_etl_run_log_batch_id_status ON audit.etl_run_log(batch_id, status);
CREATE INDEX IF NOT EXISTS idx_etl_run_log_started_at ON audit.etl_run_log(started_at);
CREATE INDEX IF NOT EXISTS idx_dq_issue_summary_created_at ON audit.data_quality_issue_summary(created_at);
CREATE INDEX IF NOT EXISTS idx_dq_table_summary_created_batch
  ON audit.data_quality_table_summary(created_at DESC, batch_id DESC);
CREATE INDEX IF NOT EXISTS idx_customer_monthly_order_summary_month
  ON audit.customer_monthly_order_summary(order_month, customer_id);