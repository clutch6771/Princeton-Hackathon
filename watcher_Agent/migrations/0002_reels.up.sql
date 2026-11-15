-- 0002: reels (PRD record + operational extension) and the single thumbnail.
--
-- Schema extension over the PRD: evaluation_status and a NULLABLE verdict.
-- Only judged reels carry a verdict; a reel that could not be judged is
-- never stored as 'neutral'.

CREATE TABLE judge.reels (
    reel_id                 uuid PRIMARY KEY,
    owner_id                text NOT NULL,
    session_id              uuid NOT NULL REFERENCES judge.sessions ON DELETE CASCADE,
    source_id               text NOT NULL,
    order_seq               bigint NOT NULL CHECK (order_seq >= 1),
    idempotency_key         text NOT NULL,
    start_ms                bigint NOT NULL,
    end_ms                  bigint NOT NULL,
    boundary_uncertainty_ms integer NOT NULL CHECK (boundary_uncertainty_ms >= 0),
    first_second_observed   boolean NOT NULL,
    segmentation_status     text NOT NULL
                            CHECK (segmentation_status IN
                                   ('clean', 'approximate', 'uncertain')),
    close_reason            text NOT NULL
                            CHECK (close_reason IN
                                   ('boundary', 'left_feed', 'frame_gap',
                                    'max_duration', 'session_end')),
    evaluation_status       text NOT NULL DEFAULT 'pending'
                            CHECK (evaluation_status IN
                                   ('pending', 'judged', 'insufficient_evidence',
                                    'values_unavailable', 'failed_provider',
                                    'failed_validation', 'failed_timeout',
                                    'skipped_overload', 'lost_on_restart')),
    verdict                 text CHECK (verdict IN ('aligned', 'neutral', 'conflict')),
    confidence              double precision
                            CHECK (confidence >= 0 AND confidence <= 1),
    transcript              text NOT NULL DEFAULT '',
    on_screen_text          text NOT NULL DEFAULT '',
    description             text NOT NULL DEFAULT '',
    features                jsonb,
    value_ids               text[] NOT NULL DEFAULT '{}',
    reason                  text NOT NULL DEFAULT '',
    coverage                jsonb,
    audio_status            text,
    transcript_status       text,
    values_revision         text,
    values_sha256           text,
    feedback_example_ids    uuid[] NOT NULL DEFAULT '{}',
    retrieval_mode          text,
    model_id                text,
    prompt_version          text,
    understanding_attempts  integer NOT NULL DEFAULT 0
                            CHECK (understanding_attempts >= 0),
    timings                 jsonb,
    latency_overrun         boolean,
    error_code              text,
    batch_id                uuid,
    batch_position          integer,
    has_thumbnail           boolean NOT NULL DEFAULT false,
    created_at              timestamptz NOT NULL,
    completed_at            timestamptz,
    UNIQUE (session_id, order_seq),
    UNIQUE (owner_id, idempotency_key),
    CONSTRAINT reels_verdict_only_when_judged
        CHECK ((evaluation_status = 'judged') = (verdict IS NOT NULL))
);

CREATE INDEX reels_timeline_idx ON judge.reels (owner_id, start_ms DESC, reel_id DESC);
CREATE INDEX reels_pending_idx ON judge.reels (created_at)
    WHERE evaluation_status = 'pending';
CREATE INDEX reels_batch_idx ON judge.reels (batch_id, batch_position)
    WHERE batch_id IS NOT NULL;

-- At most one sanitized, bounded JPEG per reel (primary key = reel).
CREATE TABLE judge.reel_thumbnails (
    reel_id    uuid PRIMARY KEY REFERENCES judge.reels ON DELETE CASCADE,
    owner_id   text NOT NULL,
    jpeg       bytea NOT NULL CHECK (octet_length(jpeg) BETWEEN 1 AND 65536),
    width      integer NOT NULL CHECK (width > 0),
    height     integer NOT NULL CHECK (height > 0),
    sha256     text NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now()
);
