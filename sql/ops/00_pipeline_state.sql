-- Where each pipeline stage stopped, per source table. Silver reads "bronze batches after
-- last_batch_id" so each run only touches new data (incremental load).
CREATE TABLE IF NOT EXISTS ops.pipeline_state (
    stage         text        NOT NULL,   -- 'silver', later 'gold'
    table_name    text        NOT NULL,
    last_batch_id bigint      NOT NULL DEFAULT 0,
    updated_at    timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (stage, table_name)
);
