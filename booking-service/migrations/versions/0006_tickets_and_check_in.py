"""Tickets and check-in (Phase 4, ADR 0015).

- booking_tickets: where each booking's PDF ticket is, as notification-service announced it (ticket.rendered).
  booking-service learns it from the event, never from notification-service's database.
- bookings.checked_in_at / checked_in_by: set once, by the first valid scan on the day of departure.

Revision ID: 0006
Revises: 0005
Create Date: 2026-11-04
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0006"
down_revision: str | None = "0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "booking_tickets",
        sa.Column("booking_id", sa.BigInteger(), sa.ForeignKey("bookings.id"), primary_key=True),
        sa.Column("bucket", sa.String(63), nullable=False),
        sa.Column("object_key", sa.String(1024), nullable=False),
        sa.Column("sha256", sa.String(64), nullable=False),
        sa.Column("size_bytes", sa.Integer(), nullable=False),
        # The ticket.rendered event that told us, so a replayed or older event changes nothing.
        sa.Column("event_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("rendered_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("recorded_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.add_column("bookings", sa.Column("checked_in_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("bookings", sa.Column("checked_in_by", sa.String(255), nullable=True))
    op.create_check_constraint(
        "ck_bookings_checked_in_by", "bookings", "(checked_in_at IS NULL) = (checked_in_by IS NULL)"
    )


def downgrade() -> None:
    op.drop_constraint("ck_bookings_checked_in_by", "bookings", type_="check")
    op.drop_column("bookings", "checked_in_by")
    op.drop_column("bookings", "checked_in_at")
    op.drop_table("booking_tickets")
