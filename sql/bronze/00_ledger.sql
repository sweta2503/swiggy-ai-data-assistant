-- Bronze: raw landing. Data is kept AS RECEIVED (everything is text) plus load metadata.
-- Re-runnable: every statement is IF NOT EXISTS.

-- One batch id per loaded file. silver reads "everything after the last batch I processed".
CREATE SEQUENCE IF NOT EXISTS bronze.batch_seq;

-- The ledger is what makes loads idempotent: a file whose content hash is already here is skipped.
CREATE TABLE IF NOT EXISTS bronze.load_ledger (
    file_hash      text PRIMARY KEY,          -- sha256 of the file's bytes
    source_file    text        NOT NULL,      -- path relative to the data folder
    row_count      integer     NOT NULL,      -- rows loaded into the bronze table
    rejected_count integer     NOT NULL DEFAULT 0,
    batch_id       bigint      NOT NULL,
    loaded_at      timestamptz NOT NULL DEFAULT now(),
    status         text        NOT NULL CHECK (status IN ('loaded', 'rejected_header'))
);
CREATE INDEX IF NOT EXISTS load_ledger_source_file_idx ON bronze.load_ledger (source_file);

-- Rows (or whole files) that could not be parsed. Nothing is silently dropped.
CREATE TABLE IF NOT EXISTS bronze.rejected_files_rows (
    reject_id   bigserial PRIMARY KEY,
    source_file text        NOT NULL,
    file_hash   text        NOT NULL,
    batch_id    bigint      NOT NULL,
    row_number  integer,                       -- NULL when the whole file was rejected
    raw_content text,
    reason      text        NOT NULL,
    rejected_at timestamptz NOT NULL DEFAULT now()
);
