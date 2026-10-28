# 3. pgvector instead of ChromaDB for embeddings

Date: 2026-10-27 · Status: Accepted

## Context
Embeddings were stored in an in-memory `chromadb.Client()`, so every restart lost them. A persistent
ChromaDB would add another datastore to run, back up and monitor.

## Decision
Store embeddings in PostgreSQL with the pgvector extension, with an HNSW index using cosine distance.

## Consequences
- Same operational tooling as every other service (backups, migrations, monitoring).
- Upserts are transactional and idempotent, which suits at-least-once event delivery.
- Enables the Phase 6 hybrid query: semantic similarity plus a geographic filter in one SQL statement.
- A dedicated vector database would scale further for hundreds of millions of vectors. That isn't this workload.
