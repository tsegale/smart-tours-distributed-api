"""notification-service schema: processed events, booking snapshots, notifications, tickets and an outbox.

Revision ID: 0001
Revises:
Create Date: 2026-11-04
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "processed_events",
        sa.Column("event_id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("event_type", sa.String(100), nullable=False),
        sa.Column("processed_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )

    op.create_table(
        "booking_snapshots",
        sa.Column("booking_id", sa.BigInteger(), primary_key=True, autoincrement=False),
        sa.Column("reference", sa.String(16), nullable=False),
        sa.Column("user_id", sa.String(255), nullable=False),
        sa.Column("contact_email", sa.String(320), nullable=True),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("status_changed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("slots", sa.Integer(), nullable=False),
        sa.Column("total_amount", sa.String(20), nullable=False),
        sa.Column("currency", sa.String(3), nullable=False),
        sa.Column("refund_amount", sa.String(20), nullable=True),
        sa.Column("availability_id", sa.BigInteger(), nullable=False),
        sa.Column("tour_id", sa.BigInteger(), nullable=False),
        sa.Column("tour_name", sa.String(255), nullable=False),
        sa.Column("meeting_point", sa.String(500), nullable=True),
        sa.Column("time_zone", sa.String(64), nullable=False),
        sa.Column("starts_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index(
        "ix_booking_snapshots_reminders",
        "booking_snapshots",
        ["starts_at"],
        postgresql_where=sa.text("status = 'CONFIRMED'"),
    )

    op.create_table(
        "notifications",
        sa.Column("id", sa.BigInteger(), sa.Identity(), primary_key=True),
        sa.Column("booking_id", sa.BigInteger(), nullable=False),
        sa.Column("type", sa.String(20), nullable=False),
        sa.Column("status", sa.String(20), nullable=False, server_default="PENDING"),
        sa.Column("recipient", sa.String(320), nullable=False),
        sa.Column("message_id", sa.String(255), nullable=False),
        sa.Column("event_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("dispatched_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("booking_id", "type", name="uq_notifications_booking_type"),
        sa.CheckConstraint("status IN ('PENDING', 'SENT', 'SKIPPED', 'FAILED')", name="ck_notifications_status"),
    )
    op.create_index(
        "ix_notifications_pending", "notifications", ["created_at"], postgresql_where=sa.text("status = 'PENDING'")
    )

    op.create_table(
        "tickets",
        sa.Column("booking_id", sa.BigInteger(), primary_key=True, autoincrement=False),
        sa.Column("reference", sa.String(16), nullable=False),
        sa.Column("bucket", sa.String(63), nullable=False),
        sa.Column("object_key", sa.String(1024), nullable=False),
        sa.Column("size_bytes", sa.Integer(), nullable=False),
        sa.Column("sha256", sa.String(64), nullable=False),
        sa.Column("token_expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("rendered_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )

    op.create_table(
        "outbox",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("aggregate_type", sa.String(50), nullable=False),
        sa.Column("aggregate_id", sa.String(64), nullable=False),
        sa.Column("event_type", sa.String(100), nullable=False),
        sa.Column("payload", postgresql.JSONB(), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index(
        "ix_outbox_unpublished", "outbox", ["occurred_at", "id"], postgresql_where=sa.text("published_at IS NULL")
    )
    op.create_index(
        "ix_outbox_published_at", "outbox", ["published_at"], postgresql_where=sa.text("published_at IS NOT NULL")
    )


def downgrade() -> None:
    op.drop_table("outbox")
    op.drop_table("tickets")
    op.drop_index("ix_notifications_pending", table_name="notifications")
    op.drop_table("notifications")
    op.drop_index("ix_booking_snapshots_reminders", table_name="booking_snapshots")
    op.drop_table("booking_snapshots")
    op.drop_table("processed_events")
