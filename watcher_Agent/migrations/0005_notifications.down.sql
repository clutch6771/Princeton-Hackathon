-- Rollback of 0005. Destroys policy, batches, outbox and cooldown state.
ALTER TABLE IF EXISTS judge.reels DROP CONSTRAINT IF EXISTS reels_batch_fk;
DROP TABLE IF EXISTS judge.user_notification_state;
DROP TABLE IF EXISTS judge.notification_outbox;
DROP TABLE IF EXISTS judge.notification_batches;
DROP TABLE IF EXISTS judge.notification_policy;
