"""Who booked and rated what, from booking events, for personalised recommendations (ADR 0022).

Only what ranking needs: a user id, never the booking's contact details. A review event names a booking and not a
user, and may arrive before its booking's event, so the two tables are joined when read.

Revision ID: 0004
Revises: 0003
Create Date: 2026-11-06
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE ai_bookings (
            booking_id  BIGINT PRIMARY KEY,
            user_id     VARCHAR(255) NOT NULL,
            tour_id     BIGINT NOT NULL,
            confirmed   BOOLEAN NOT NULL,
            occurred_at TIMESTAMPTZ NOT NULL
        )
        """
    )
    op.execute("CREATE INDEX ix_ai_bookings_user ON ai_bookings (user_id) WHERE confirmed")
    op.execute("CREATE INDEX ix_ai_bookings_tour ON ai_bookings (tour_id) WHERE confirmed")
    op.execute(
        """
        CREATE TABLE ai_reviews (
            booking_id  BIGINT PRIMARY KEY,
            tour_id     BIGINT NOT NULL,
            rating      SMALLINT NOT NULL CHECK (rating BETWEEN 1 AND 5),
            occurred_at TIMESTAMPTZ NOT NULL
        )
        """
    )


def downgrade() -> None:
    op.execute("DROP TABLE ai_reviews")
    op.execute("DROP TABLE ai_bookings")
