-- Rollback of 0001. Destroys all Judge data.
DROP TABLE IF EXISTS judge.ingest_receipts;
DROP TABLE IF EXISTS judge.session_events;
DROP TABLE IF EXISTS judge.sessions;
DROP SCHEMA IF EXISTS judge;
