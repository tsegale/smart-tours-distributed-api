"""pgvector-backed tour embeddings (replaces the in-memory ChromaDB collection).

Revision ID: 0001
Revises:
Create Date: 2026-10-27
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")
    op.execute(
        """
        CREATE TABLE tour_embeddings (
            tour_id       BIGINT PRIMARY KEY,
            title         TEXT         NOT NULL,
            content       TEXT         NOT NULL,
            embedding     vector(384)  NOT NULL,
            model         VARCHAR(200) NOT NULL,
            last_event_id UUID,
            updated_at    TIMESTAMPTZ  NOT NULL DEFAULT now()
        )
        """
    )
    # HNSW: approximate nearest-neighbour index, so similarity queries stay fast as tours grow.
    op.execute("CREATE INDEX ix_tour_embeddings_hnsw ON tour_embeddings USING hnsw (embedding vector_cosine_ops)")


def downgrade() -> None:
    op.execute("DROP TABLE tour_embeddings")
