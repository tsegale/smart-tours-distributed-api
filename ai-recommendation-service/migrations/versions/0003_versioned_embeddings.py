"""One vector per tour per model, so a new model can be backfilled while reads stay on the old one (ADR 0020).

Revision ID: 0003
Revises: 0002
Create Date: 2026-11-06
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Place names load from a file in the image (app/gazetteer.py): nothing ever needed them in a table.
    op.execute("DROP TABLE gazetteer")
    op.execute("ALTER TABLE tour_embeddings DROP CONSTRAINT tour_embeddings_pkey")
    op.execute("ALTER TABLE tour_embeddings ADD PRIMARY KEY (tour_id, model)")
    op.execute(
        """
        CREATE TABLE embedding_models (
            model      VARCHAR(200) PRIMARY KEY,
            state      VARCHAR(20) NOT NULL CHECK (state IN ('active', 'backfilling', 'retired')),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )
    op.execute("CREATE UNIQUE INDEX ux_embedding_models_one_active ON embedding_models (state) WHERE state = 'active'")


def downgrade() -> None:
    op.execute("DROP TABLE embedding_models")
    op.execute(
        """
        DELETE FROM tour_embeddings a USING tour_embeddings b
        WHERE a.tour_id = b.tour_id AND (a.updated_at, a.model) < (b.updated_at, b.model)
        """
    )
    op.execute("ALTER TABLE tour_embeddings DROP CONSTRAINT tour_embeddings_pkey")
    op.execute("ALTER TABLE tour_embeddings ADD PRIMARY KEY (tour_id)")
    op.execute(
        """
        CREATE TABLE gazetteer (
            geoname_id  BIGINT PRIMARY KEY,
            name        TEXT NOT NULL,
            match_name  TEXT NOT NULL,
            country     VARCHAR(2) NOT NULL,
            location    geography(Point, 4326) NOT NULL,
            population  BIGINT NOT NULL DEFAULT 0
        )
        """
    )
    op.execute("CREATE INDEX ix_gazetteer_match ON gazetteer (match_name)")
