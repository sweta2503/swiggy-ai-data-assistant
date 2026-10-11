-- The audit trail: one row per question, and one row per query attempt.
-- Stored text has been scrubbed of phone numbers, emails, card and ID numbers and API keys.
-- Nothing here is readable by the assistant (it has no access to the ops schema).
CREATE TABLE IF NOT EXISTS ops.assistant_runs (
    run_id          uuid PRIMARY KEY,
    thread_id       text,
    user_id         text        NOT NULL,
    scope           text        NOT NULL,              -- the city scope the question ran with
    ts              timestamptz NOT NULL DEFAULT now(),
    question        text        NOT NULL,              -- as typed (scrubbed)
    working_question text,                             -- as understood after follow-ups (scrubbed)
    route           text,                              -- simple | multi_step | ambiguous | unsafe | ...
    status          text        NOT NULL CHECK (status IN ('answered', 'clarify', 'refused', 'failed')),
    healed          boolean     NOT NULL DEFAULT false, -- answered only after a repair
    attempts_count  integer     NOT NULL DEFAULT 0,
    steps           integer     NOT NULL DEFAULT 0,
    final_sql       text,                              -- (scrubbed)
    latency_ms      integer,
    tokens_in       integer     NOT NULL DEFAULT 0,
    tokens_out      integer     NOT NULL DEFAULT 0,
    est_cost        numeric(12, 6),                    -- US dollars; NULL = prices were not set
    usage_by_step   jsonb,
    models          jsonb,                             -- which model each tier used
    checks          jsonb,                             -- result-checker findings
    warnings        jsonb,
    fault_injected  boolean     NOT NULL DEFAULT false
);
CREATE INDEX IF NOT EXISTS assistant_runs_ts_idx   ON ops.assistant_runs (ts DESC);
CREATE INDEX IF NOT EXISTS assistant_runs_user_idx ON ops.assistant_runs (user_id, ts DESC);

CREATE TABLE IF NOT EXISTS ops.assistant_attempts (
    run_id       uuid        NOT NULL REFERENCES ops.assistant_runs (run_id) ON DELETE CASCADE,
    attempt_no   integer     NOT NULL,                 -- 1, 2, 3 ... across the whole question
    step         integer     NOT NULL DEFAULT 1,       -- which sub-question of a multi-step plan
    source       text,                                 -- writer | fault | healer | repair
    sql          text,                                 -- (scrubbed)
    status       text        NOT NULL,                 -- ok | error | rejected
    error_class  text,
    error_text   text,                                 -- (scrubbed)
    latency_ms   integer,
    PRIMARY KEY (run_id, attempt_no)
);
CREATE INDEX IF NOT EXISTS assistant_attempts_class_idx ON ops.assistant_attempts (error_class);
