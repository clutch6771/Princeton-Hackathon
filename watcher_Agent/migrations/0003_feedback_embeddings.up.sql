-- 0003: explicit feedback, and embedding indexing metadata.
--
-- The metadata table needs no extension, so indexing status is tracked even
-- on a service where pgvector is not enabled. Vectors live in 0004.

CREATE TABLE judge.feedback (
    feedback_id        uuid PRIMARY KEY,
    owner_id           text NOT NULL,
    reel_id            uuid NOT NULL REFERENCES judge.reels ON DELETE CASCADE,
    label              text NOT NULL
                       CHECK (label IN ('agree', 'disagree', 'not_for_me', 'rerate')),
    score              double precision,
    comment            text,
    source             text,
    client_feedback_id uuid,
    created_at         timestamptz NOT NULL,
    UNIQUE (owner_id, client_feedback_id)
);

CREATE INDEX feedback_owner_idx ON judge.feedback (owner_id, created_at DESC);
CREATE INDEX feedback_reel_idx ON judge.feedback (reel_id);

CREATE TABLE judge.reel_embeddings (
    reel_id         uuid NOT NULL REFERENCES judge.reels ON DELETE CASCADE,
    profile_id      text NOT NULL,
    owner_id        text NOT NULL,
    model_id        text NOT NULL,
    dimension       integer NOT NULL CHECK (dimension > 0),
    task_type       text NOT NULL,
    status          text NOT NULL DEFAULT 'pending'
                    CHECK (status IN ('pending', 'indexed', 'failed')),
    attempts        integer NOT NULL DEFAULT 0 CHECK (attempts >= 0),
    next_attempt_at timestamptz,
    error_code      text,
    created_at      timestamptz NOT NULL,
    indexed_at      timestamptz,
    PRIMARY KEY (reel_id, profile_id)
);

CREATE INDEX reel_embeddings_due_idx ON judge.reel_embeddings (next_attempt_at)
    WHERE status = 'pending';
