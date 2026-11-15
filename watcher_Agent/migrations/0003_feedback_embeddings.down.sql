-- Rollback of 0003. Destroys feedback and embedding metadata.
DROP TABLE IF EXISTS judge.reel_embeddings;
DROP TABLE IF EXISTS judge.feedback;
