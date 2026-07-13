CREATE SCHEMA IF NOT EXISTS raw;
CREATE SCHEMA IF NOT EXISTS core;
CREATE SCHEMA IF NOT EXISTS audit;

-- =========================
-- RAW
-- =========================
CREATE TABLE IF NOT EXISTS raw.orders_raw (
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

CREATE TABLE IF NOT EXISTS raw.customer_raw (
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

CREATE TABLE IF NOT EXISTS raw.survey_raw (
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
  rows_orders_raw INT,
  rows_customer_raw INT,
  rows_survey_raw INT,
  rows_orders_core INT,
  rows_customer_core INT,
  rows_survey_core INT,
  error_message TEXT
);

-- =========================
-- INDEXES
-- =========================
CREATE INDEX IF NOT EXISTS idx_customer_email_norm ON core.customer(email_norm);
CREATE INDEX IF NOT EXISTS idx_orders_customer_id ON core.orders(customer_id);
CREATE INDEX IF NOT EXISTS idx_orders_order_date ON core.orders(order_date);
CREATE INDEX IF NOT EXISTS idx_survey_customer_id ON core.survey(customer_id);
CREATE INDEX IF NOT EXISTS idx_survey_order_id ON core.survey(order_id);