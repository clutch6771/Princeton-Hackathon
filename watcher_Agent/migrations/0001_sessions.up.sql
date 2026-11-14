-- 0001: sessions, lifecycle events, ingest receipts.
-- No table in the judge schema ever stores raw audio, video or extra frames.

CREATE SCHEMA IF NOT EXISTS judge;

CREATE TABLE judge.sessions (
    session_id        uuid PRIMARY KEY,
    owner_id          text NOT NULL,
    source_id         text NOT NULL,
    status            text NOT NULL DEFAULT 'active'
                      CHECK (status IN ('active', 'ending', 'ended')),
    started_at        timestamptz NOT NULL,
    last_ingest_at    timestamptz NOT NULL,
    ended_at          timestamptz,
    end_reason        text CHECK (end_reason IN ('client_end', 'idle_timeout')),
    next_order_seq    bigint NOT NULL DEFAULT 1 CHECK (next_order_seq >= 1),
    accounting_cursor bigint NOT NULL DEFAULT 0 CHECK (accounting_cursor >= 0),
    judged_count      integer NOT NULL DEFAULT 0 CHECK (judged_count >= 0),
    conflict_count    integer NOT NULL DEFAULT 0 CHECK (conflict_count >= 0),
    excluded_count    integer NOT NULL DEFAULT 0 CHECK (excluded_count >= 0)
);

CREATE INDEX sessions_owner_idx ON judge.sessions (owner_id, started_at DESC);
CREATE INDEX sessions_active_idx ON judge.sessions (last_ingest_at)
    WHERE status = 'active';

-- Lifecycle events for future consumers. Writing one invokes nobody.
CREATE TABLE judge.session_events (
    event_id   uuid PRIMARY KEY,
    session_id uuid NOT NULL REFERENCES judge.sessions ON DELETE CASCADE,
    owner_id   text NOT NULL,
    event_type text NOT NULL,
    payload    jsonb NOT NULL,
    created_at timestamptz NOT NULL,
    UNIQUE (session_id, event_type)
);

-- Idempotency and metadata only. Never the uploaded payload.
CREATE TABLE judge.ingest_receipts (
    event_id         uuid PRIMARY KEY,
    owner_id         text NOT NULL,
    session_id       uuid NOT NULL REFERENCES judge.sessions ON DELETE CASCADE,
    kind             text NOT NULL CHECK (kind IN ('frame', 'audio')),
    source_seq       bigint NOT NULL,
    capture_start_ms bigint NOT NULL,
    capture_end_ms   bigint NOT NULL,
    received_at      timestamptz NOT NULL,
    byte_length      integer NOT NULL CHECK (byte_length >= 0),
    outcome          text NOT NULL,
    process_epoch    uuid NOT NULL
);

CREATE INDEX ingest_receipts_received_idx ON judge.ingest_receipts (received_at);
CREATE INDEX ingest_receipts_session_idx ON judge.ingest_receipts (session_id);
