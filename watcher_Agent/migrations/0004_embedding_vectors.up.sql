-- 0004: embedding vectors. Requires the pgvector extension.
--
-- The dimension (768) is pinned to the configured embedding profile. A
-- different model, dimension or task configuration is a different vector
-- space: it needs a new, explicitly versioned migration, never an in-place
-- change, and vectors from different profiles are never compared.
-- Search is exact (no ANN index). 768 is below pgvector's 2000-dimension
-- index limit should an index be added later.

CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE judge.reel_embedding_vectors (
    reel_id    uuid NOT NULL,
    profile_id text NOT NULL,
    owner_id   text NOT NULL,
    embedding  vector(768) NOT NULL,
    PRIMARY KEY (reel_id, profile_id),
    FOREIGN KEY (reel_id, profile_id)
        REFERENCES judge.reel_embeddings (reel_id, profile_id) ON DELETE CASCADE
);

CREATE INDEX reel_embedding_vectors_owner_idx
    ON judge.reel_embedding_vectors (owner_id, profile_id);
