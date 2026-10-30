"""Verified reviews: one per booking, only for confirmed bookings whose departure has started.

Revision ID: 0004
Revises: 0003
Create Date: 2026-10-30
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "reviews",
        sa.Column("id", sa.BigInteger(), sa.Identity(), primary_key=True),
        sa.Column("booking_id", sa.BigInteger(), sa.ForeignKey("bookings.id"), nullable=False),
        sa.Column("user_id", sa.String(255), nullable=False),
        # Owned by tour-core-service: a plain id, no foreign key across services.
        sa.Column("tour_id", sa.BigInteger(), nullable=False),
        sa.Column("rating", sa.SmallInteger(), nullable=False),
        sa.Column("comment", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint("rating BETWEEN 1 AND 5", name="ck_reviews_rating_range"),
        sa.CheckConstraint("char_length(comment) <= 2000", name="ck_reviews_comment_length"),
        # The rule "one review per booking" lives in the database, not only in the endpoint.
        sa.UniqueConstraint("booking_id", name="uq_reviews_booking_id"),
    )
    op.create_index("ix_reviews_tour_id", "reviews", ["tour_id"])


def downgrade() -> None:
    op.drop_index("ix_reviews_tour_id", table_name="reviews")
    op.drop_table("reviews")
