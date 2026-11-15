-- Rollback of 0004. Destroys stored vectors. The extension is left in place
-- because other schemas on the service may use it.
DROP TABLE IF EXISTS judge.reel_embedding_vectors;
