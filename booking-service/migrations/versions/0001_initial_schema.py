"""Initial schema: departures and bookings with overbooking guards.

Revision ID: 0001
Revises:
Create Date: 2026-10-27
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "tour_availability",
        sa.Column("id", sa.BigInteger(), sa.Identity(), primary_key=True),
        sa.Column("tour_id", sa.BigInteger(), nullable=False),
        sa.Column("starts_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("total_slots", sa.Integer(), nullable=False),
        sa.Column("booked_slots", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint("total_slots > 0", name="ck_availability_total_positive"),
        sa.CheckConstraint("booked_slots >= 0 AND booked_slots <= total_slots", name="ck_availability_not_overbooked"),
        sa.UniqueConstraint("tour_id", "starts_at", name="uq_availability_tour_start"),
    )
    op.create_index("ix_tour_availability_tour_id", "tour_availability", ["tour_id"])

    op.create_table(
        "bookings",
        sa.Column("id", sa.BigInteger(), sa.Identity(), primary_key=True),
        sa.Column("user_id", sa.String(255), nullable=False),
        sa.Column("availability_id", sa.BigInteger(), sa.ForeignKey("tour_availability.id"), nullable=False),
        sa.Column("slots", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(20), nullable=False, server_default="CONFIRMED"),
        sa.Column("idempotency_key", sa.String(255), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("cancelled_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("slots > 0", name="ck_bookings_slots_positive"),
        sa.CheckConstraint(
            "status IN ('PENDING_PAYMENT', 'CONFIRMED', 'CANCELLED', 'EXPIRED')", name="ck_bookings_status"
        ),
        sa.UniqueConstraint("user_id", "idempotency_key", name="uq_bookings_user_idempotency_key"),
    )
    op.create_index("ix_bookings_user_id", "bookings", ["user_id"])
    op.create_index("ix_bookings_availability_id", "bookings", ["availability_id"])


def downgrade() -> None:
    op.drop_table("bookings")
    op.drop_table("tour_availability")
