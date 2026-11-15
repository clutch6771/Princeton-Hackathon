-- 0005: notification policy, batches, outbox and the per-user cooldown anchor.

CREATE TABLE judge.notification_policy (
    owner_id          text NOT NULL,
    version           integer NOT NULL CHECK (version >= 1),
    batch_size        integer NOT NULL CHECK (batch_size BETWEEN 1 AND 100),
    minimum_conflicts integer NOT NULL,
    cooldown_seconds  integer NOT NULL CHECK (cooldown_seconds BETWEEN 0 AND 86400),
    updated_at        timestamptz NOT NULL,
    PRIMARY KEY (owner_id, version),
    CONSTRAINT notification_policy_conflicts_within_batch
        CHECK (minimum_conflicts BETWEEN 1 AND batch_size)
);

-- Non-overlapping, session-scoped batches of judged reels in capture order.
CREATE TABLE judge.notification_batches (
    batch_id          uuid PRIMARY KEY,
    owner_id          text NOT NULL,
    session_id        uuid NOT NULL REFERENCES judge.sessions ON DELETE CASCADE,
    batch_index       integer NOT NULL CHECK (batch_index >= 1),
    policy_version    integer NOT NULL CHECK (policy_version >= 0),
    batch_size        integer NOT NULL CHECK (batch_size >= 1),
    minimum_conflicts integer NOT NULL CHECK (minimum_conflicts >= 1),
    cooldown_seconds  integer NOT NULL CHECK (cooldown_seconds >= 0),
    status            text NOT NULL DEFAULT 'open'
                      CHECK (status IN ('open', 'evaluated')),
    judged_count      integer NOT NULL DEFAULT 0 CHECK (judged_count >= 0),
    conflict_count    integer NOT NULL DEFAULT 0 CHECK (conflict_count >= 0),
    excluded_count    integer NOT NULL DEFAULT 0 CHECK (excluded_count >= 0),
    outcome           text CHECK (outcome IN
                                  ('triggered', 'below_threshold',
                                   'suppressed_cooldown', 'suppressed_pending',
                                   'incomplete_tail')),
    opened_at         timestamptz NOT NULL,
    evaluated_at      timestamptz,
    UNIQUE (session_id, batch_index),
    CONSTRAINT notification_batches_outcome_when_evaluated
        CHECK ((status = 'evaluated') = (outcome IS NOT NULL))
);

-- A session has at most one open batch.
CREATE UNIQUE INDEX notification_batches_one_open_idx
    ON judge.notification_batches (session_id) WHERE status = 'open';

ALTER TABLE judge.reels
    ADD CONSTRAINT reels_batch_fk
    FOREIGN KEY (batch_id) REFERENCES judge.notification_batches ON DELETE SET NULL;

-- One warning per triggered batch (batch_id is unique).
CREATE TABLE judge.notification_outbox (
    notification_id   uuid PRIMARY KEY,
    owner_id          text NOT NULL,
    session_id        uuid NOT NULL REFERENCES judge.sessions ON DELETE CASCADE,
    batch_id          uuid NOT NULL UNIQUE
                      REFERENCES judge.notification_batches ON DELETE CASCADE,
    title             text NOT NULL,
    body              text NOT NULL,
    reel_ids          uuid[] NOT NULL,
    cooldown_seconds  integer NOT NULL CHECK (cooldown_seconds >= 0),
    status            text NOT NULL DEFAULT 'pending'
                      CHECK (status IN
                             ('pending', 'leased', 'acknowledged', 'expired',
                              'cancelled', 'unacknowledged')),
    created_at        timestamptz NOT NULL,
    expires_at        timestamptz NOT NULL,
    lease_until       timestamptz,
    dispatch_attempts integer NOT NULL DEFAULT 0 CHECK (dispatch_attempts >= 0),
    dispatched_at     timestamptz,
    acknowledged_at   timestamptz,
    closed_reason     text
);

CREATE INDEX notification_outbox_undelivered_idx
    ON judge.notification_outbox (owner_id, created_at)
    WHERE status IN ('pending', 'leased');

-- Cooldown is per user, across sessions. The row is locked FOR UPDATE to
-- serialize batch evaluation and dispatch decisions.
CREATE TABLE judge.user_notification_state (
    owner_id         text PRIMARY KEY,
    last_dispatch_at timestamptz
);
