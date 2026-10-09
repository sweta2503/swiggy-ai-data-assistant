-- Data quality results. One row per check per run.
CREATE TABLE IF NOT EXISTS ops.dq_results (
    result_id  bigserial PRIMARY KEY,
    run_id     uuid        NOT NULL,
    run_kind   text        NOT NULL DEFAULT 'standard' CHECK (run_kind IN ('standard', 'demo')),
    check_name text        NOT NULL,
    layer      text        NOT NULL CHECK (layer IN ('bronze', 'silver', 'gold')),
    table_name text        NOT NULL,
    severity   text        NOT NULL CHECK (severity IN ('blocking', 'warning')),
    status     text        NOT NULL CHECK (status IN ('pass', 'fail', 'error')),
    observed   numeric,                 -- what we measured (bad rows, hours late, ratio...)
    threshold  numeric,                 -- the most we accept
    detail     text,
    run_ts     timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS dq_results_run_idx ON ops.dq_results (run_id);
CREATE INDEX IF NOT EXISTS dq_results_ts_idx  ON ops.dq_results (run_ts DESC);

-- The most recent standard run. Demo runs never count toward "is the data healthy?".
CREATE OR REPLACE VIEW ops.dq_latest_run AS
SELECT * FROM ops.dq_results
WHERE run_id = (SELECT run_id FROM ops.dq_results WHERE run_kind = 'standard'
                ORDER BY run_ts DESC, result_id DESC LIMIT 1);
