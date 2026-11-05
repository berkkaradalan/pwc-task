-- Runs once when the database volume is created.
-- Tables are created by the application, because the vector column dimension
-- comes from EMBEDDING_DIM (see shared/adapters/vectorstore/pgvector.py).
CREATE EXTENSION IF NOT EXISTS vector;
