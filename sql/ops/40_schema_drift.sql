-- The columns each source file type is expected to have, and an alert for every file that differs.
CREATE TABLE IF NOT EXISTS ops.expected_schemas (
    file_type   text PRIMARY KEY,              -- bronze table name, e.g. 'orders'
    columns     jsonb       NOT NULL,          -- the column names, as a list
    fingerprint text        NOT NULL,          -- sha-256 of the sorted column names
    updated_at  timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS ops.schema_alerts (
    alert_id             bigserial PRIMARY KEY,
    file_type            text        NOT NULL,
    source_file          text        NOT NULL,
    file_hash            text        NOT NULL UNIQUE,  -- one alert per file, however often it is seen
    expected_fingerprint text        NOT NULL,
    observed_fingerprint text        NOT NULL,
    missing_columns      jsonb       NOT NULL,         -- expected but absent from the file
    new_columns          jsonb       NOT NULL,         -- in the file but not expected
    suggested_renames    jsonb       NOT NULL DEFAULT '{}',  -- {name in file: name we expect}
    status               text        NOT NULL DEFAULT 'open' CHECK (status IN ('open', 'resolved')),
    detected_at          timestamptz NOT NULL DEFAULT now(),
    resolved_at          timestamptz,
    resolution           text
);
CREATE INDEX IF NOT EXISTS schema_alerts_open_idx ON ops.schema_alerts (status);
