"""The AI service's read model: everything a hybrid query filters on, next to each embedding (ADR 0019).

Revision ID: 0002
Revises: 0001
Create Date: 2026-11-06
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS postgis")
    op.execute(
        """
        CREATE TABLE ai_tours (
            tour_id           BIGINT PRIMARY KEY,
            name              TEXT NOT NULL,
            description       TEXT,
            language          VARCHAR(2) NOT NULL DEFAULT 'en',
            location          geography(Point, 4326),
            price             NUMERIC(10, 2),
            currency          VARCHAR(3),
            category          VARCHAR(30),
            difficulty        VARCHAR(30),
            duration_minutes  INTEGER,
            rating_count      INTEGER NOT NULL DEFAULT 0,
            rating_sum        BIGINT NOT NULL DEFAULT 0,
            version           BIGINT NOT NULL DEFAULT 0,
            updated_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
            -- Stemmed in the tour's own language; the name counts for more than the body.
            search_vector     tsvector GENERATED ALWAYS AS (
                CASE WHEN language = 'de'
                     THEN setweight(to_tsvector('german', coalesce(name, '')), 'A')
                          || setweight(to_tsvector('german', coalesce(description, '')), 'B')
                     ELSE setweight(to_tsvector('english', coalesce(name, '')), 'A')
                          || setweight(to_tsvector('english', coalesce(description, '')), 'B')
                END
            ) STORED
        )
        """
    )
    op.execute("CREATE INDEX ix_ai_tours_location ON ai_tours USING gist (location)")
    op.execute("CREATE INDEX ix_ai_tours_search ON ai_tours USING gin (search_vector)")
    op.execute("CREATE INDEX ix_ai_tours_price ON ai_tours (price)")
    op.execute(
        """
        CREATE TABLE ai_departures (
            availability_id BIGINT PRIMARY KEY,
            tour_id         BIGINT NOT NULL,
            starts_at       TIMESTAMPTZ NOT NULL,
            total_slots     INTEGER NOT NULL,
            slots_left      INTEGER NOT NULL,
            version         BIGINT NOT NULL
        )
        """
    )
    op.execute("CREATE INDEX ix_ai_departures_tour_start ON ai_departures (tour_id, starts_at)")
    op.execute(
        "CREATE TABLE ai_processed_events (event_id UUID PRIMARY KEY, processed_at TIMESTAMPTZ NOT NULL DEFAULT now())"
    )
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


def downgrade() -> None:
    for table in ("gazetteer", "ai_processed_events", "ai_departures", "ai_tours"):
        op.execute(f"DROP TABLE {table}")
