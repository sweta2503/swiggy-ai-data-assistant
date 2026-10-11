-- One row per run of the daily pipeline, and the counter that says "the data changed".
CREATE TABLE IF NOT EXISTS ops.pipeline_runs (
    run_id              uuid PRIMARY KEY,
    trigger             text        NOT NULL DEFAULT 'manual',   -- manual | scheduler | airflow
    started_at          timestamptz NOT NULL DEFAULT now(),
    finished_at         timestamptz,
    status              text        NOT NULL DEFAULT 'running'
                        CHECK (status IN ('running', 'published', 'blocked', 'failed')),
    changed             boolean     NOT NULL DEFAULT false,      -- did any stage add data?
    stages              jsonb       NOT NULL DEFAULT '[]',       -- name, status, seconds, detail
    dq_checks           integer,
    dq_blocking_failed  jsonb       NOT NULL DEFAULT '[]',       -- names of blocking checks that failed
    schema_alerts_open  integer     NOT NULL DEFAULT 0,
    catalog_changes     jsonb       NOT NULL DEFAULT '[]',
    data_version_before bigint,
    data_version_after  bigint,
    error               text
);
CREATE INDEX IF NOT EXISTS pipeline_runs_started_idx ON ops.pipeline_runs (started_at DESC);

-- Bumped by every successful (published) daily run that changed something. Cached answers carry
-- the version they were computed under, so a bump retires them all at once.
CREATE TABLE IF NOT EXISTS ops.data_version (
    id         integer PRIMARY KEY CHECK (id = 1),     -- exactly one row
    version    bigint      NOT NULL,
    updated_at timestamptz NOT NULL DEFAULT now(),
    run_id     uuid
);
INSERT INTO ops.data_version (id, version) VALUES (1, 1) ON CONFLICT DO NOTHING;
