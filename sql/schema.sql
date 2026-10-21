-- Reference DDL. The app creates these automatically on first start (CREATE ... IF NOT EXISTS).
-- Replace your_catalog / citi_accruals with the values in app.yaml.

CREATE SCHEMA IF NOT EXISTS your_catalog.citi_accruals;
CREATE VOLUME IF NOT EXISTS your_catalog.citi_accruals.documents;

CREATE TABLE IF NOT EXISTS your_catalog.citi_accruals.programs (
  `program_code` STRING,
  `program_name` STRING,
  `short_name` STRING,
  `annual_fee` DOUBLE,
  `include_in_accrual` BOOLEAN,
  `updated_by` STRING,
  `updated_at` TIMESTAMP
) USING DELTA;

CREATE TABLE IF NOT EXISTS your_catalog.citi_accruals.settings (
  `key` STRING,
  `value` STRING,
  `updated_by` STRING,
  `updated_at` TIMESTAMP
) USING DELTA;

CREATE TABLE IF NOT EXISTS your_catalog.citi_accruals.contracts (
  `fa_number` BIGINT,
  `program_code` STRING,
  `series` STRING,
  `base_series` STRING,
  `reopening_of` BIGINT,
  `isin` STRING,
  `trade_date` DATE,
  `issue_date` DATE,
  `maturity_date` DATE,
  `annual_fee_override` DOUBLE,
  `principal` STRING,
  `currency` STRING,
  `source` STRING,
  `source_document` STRING,
  `notes` STRING,
  `created_by` STRING,
  `created_at` TIMESTAMP,
  `updated_by` STRING,
  `updated_at` TIMESTAMP
) USING DELTA;

CREATE TABLE IF NOT EXISTS your_catalog.citi_accruals.fee_events (
  `event_id` STRING,
  `fa_number` BIGINT,
  `program_code` STRING,
  `event_date` DATE,
  `period_no` INT,
  `expected_amount` DOUBLE,
  `invoice_no` STRING,
  `line_id` STRING,
  `billed_amount` DOUBLE,
  `written_off` BOOLEAN,
  `write_off_date` DATE,
  `write_off_reason` STRING,
  `notes` STRING,
  `updated_by` STRING,
  `updated_at` TIMESTAMP
) USING DELTA;

CREATE TABLE IF NOT EXISTS your_catalog.citi_accruals.invoices (
  `invoice_no` STRING,
  `program_code` STRING,
  `invoice_date` DATE,
  `arrears_start` DATE,
  `arrears_end` DATE,
  `advance_start` DATE,
  `advance_end` DATE,
  `fee_total` DOUBLE,
  `lines_total` DOUBLE,
  `line_count` INT,
  `currency` STRING,
  `approval_date` DATE,
  `approved_by` STRING,
  `payment_date` DATE,
  `payment_ref` STRING,
  `file_name` STRING,
  `file_path` STRING,
  `file_hash` STRING,
  `source` STRING,
  `notes` STRING,
  `created_by` STRING,
  `created_at` TIMESTAMP,
  `updated_by` STRING,
  `updated_at` TIMESTAMP
) USING DELTA;

CREATE TABLE IF NOT EXISTS your_catalog.citi_accruals.invoice_lines (
  `line_id` STRING,
  `invoice_no` STRING,
  `line_no` INT,
  `series` STRING,
  `maturity_date` DATE,
  `action_date` DATE,
  `amount` DOUBLE,
  `currency` STRING,
  `fa_number` BIGINT,
  `event_id` STRING,
  `match_status` STRING,
  `match_notes` STRING,
  `resolved_by` STRING,
  `resolved_at` TIMESTAMP
) USING DELTA;

CREATE TABLE IF NOT EXISTS your_catalog.citi_accruals.data_issues (
  `issue_id` STRING,
  `category` STRING,
  `entity` STRING,
  `entity_id` STRING,
  `description` STRING,
  `amount_impact` DOUBLE,
  `status` STRING,
  `created_by` STRING,
  `created_at` TIMESTAMP,
  `resolved_by` STRING,
  `resolved_at` TIMESTAMP,
  `resolution` STRING
) USING DELTA;

CREATE TABLE IF NOT EXISTS your_catalog.citi_accruals.audit_log (
  `audit_id` STRING,
  `ts` TIMESTAMP,
  `user_name` STRING,
  `action` STRING,
  `entity` STRING,
  `entity_id` STRING,
  `field` STRING,
  `old_value` STRING,
  `new_value` STRING,
  `reason` STRING
) USING DELTA;

CREATE TABLE IF NOT EXISTS your_catalog.citi_accruals.snapshots (
  `snapshot_id` STRING,
  `as_of` DATE,
  `total_outstanding` DOUBLE,
  `event_count` INT,
  `summary_json` STRING,
  `file_path` STRING,
  `created_by` STRING,
  `created_at` TIMESTAMP
) USING DELTA;

CREATE TABLE IF NOT EXISTS your_catalog.citi_accruals.snapshot_lines (
  `snapshot_id` STRING,
  `event_id` STRING,
  `fa_number` BIGINT,
  `series` STRING,
  `program_code` STRING,
  `event_date` DATE,
  `amount` DOUBLE,
  `status` STRING,
  `invoice_no` STRING
) USING DELTA;

CREATE TABLE IF NOT EXISTS your_catalog.citi_accruals.documents (
  `doc_id` STRING,
  `doc_type` STRING,
  `file_name` STRING,
  `file_path` STRING,
  `file_hash` STRING,
  `uploaded_by` STRING,
  `uploaded_at` TIMESTAMP,
  `notes` STRING
) USING DELTA;

-- Permissions for the app's service principal (replace <app-sp>):
GRANT USE CATALOG ON CATALOG your_catalog TO `<app-sp>`;
GRANT USE SCHEMA, CREATE TABLE, SELECT, MODIFY ON SCHEMA your_catalog.citi_accruals TO `<app-sp>`;
GRANT READ VOLUME, WRITE VOLUME ON VOLUME your_catalog.citi_accruals.documents TO `<app-sp>`;

-- Useful admin queries
-- DESCRIBE HISTORY your_catalog.citi_accruals.fee_events;
-- RESTORE TABLE your_catalog.citi_accruals.fee_events TO VERSION AS OF <n>;
