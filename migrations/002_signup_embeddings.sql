-- Phase 4: sign-up text vectors for the scale layer and the daily clustering job.
-- Hashed n-gram vectors only: the sign-up text itself is never stored.
CREATE TABLE IF NOT EXISTS signup_embeddings (
    avaliacao_id text PRIMARY KEY REFERENCES events ON DELETE CASCADE,
    momento      timestamptz NOT NULL,
    vetor        real[] NOT NULL,
    cluster_id   integer
);
CREATE INDEX IF NOT EXISTS signup_embeddings_momento_idx ON signup_embeddings (momento);
