-- Answers already worked out, so the same question from the same scope does not call the model
-- again until the data changes. The key is a hash of: the normalised question, the data version
-- and the city scope, so a different scope or a newer version can never be served an old answer.
CREATE TABLE IF NOT EXISTS ops.answer_cache (
    cache_key     text PRIMARY KEY,
    question_norm text        NOT NULL,        -- scrubbed of personal data
    data_version  bigint      NOT NULL,
    scope_hash    text        NOT NULL,        -- which cities the answer was computed for
    answer_json   jsonb       NOT NULL,
    created_at    timestamptz NOT NULL DEFAULT now(),
    hits          integer     NOT NULL DEFAULT 0,
    last_hit_at   timestamptz
);
CREATE INDEX IF NOT EXISTS answer_cache_version_idx ON ops.answer_cache (data_version);
