-- Files whose columns do not match what we expect (schema drift) wait here instead of being loaded.
-- Nothing in this table feeds silver, so a changed source cannot corrupt the warehouse.
CREATE TABLE IF NOT EXISTS bronze.quarantine_files (
    quarantine_id        bigserial PRIMARY KEY,
    file_hash            text        NOT NULL UNIQUE,   -- same file seen again = same row, no repeat
    source_file          text        NOT NULL,
    file_type            text        NOT NULL,          -- the bronze table it was meant for
    observed_columns     jsonb       NOT NULL,
    observed_fingerprint text        NOT NULL,
    row_count            integer     NOT NULL,
    quarantined_at       timestamptz NOT NULL DEFAULT now(),
    status               text        NOT NULL DEFAULT 'open' CHECK (status IN ('open', 'released')),
    released_at          timestamptz
);

-- The rows themselves, kept as received (one JSON document per row) so nothing is lost.
CREATE TABLE IF NOT EXISTS bronze.quarantine_rows (
    quarantine_id bigint  NOT NULL REFERENCES bronze.quarantine_files (quarantine_id)
                          ON DELETE CASCADE,
    row_number    integer NOT NULL,
    record        jsonb   NOT NULL,
    PRIMARY KEY (quarantine_id, row_number)
);
