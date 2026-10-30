"""Transactional outbox: integration events written in the same transaction as the change they describe.

tour_availability.version (added in 0002) already increases on every seat change: the ORM bumps it
through version_id_col and the Core UPDATEs bump it explicitly. availability.changed events carry
it, so consumers can drop stale or duplicate events.

Revision ID: 0003
Revises: 0002
Create Date: 2026-10-30
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "outbox",
        # Also the event's eventId, so a consumer's dedupe key is fixed when the row is written.
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("aggregate_type", sa.String(50), nullable=False),
        sa.Column("aggregate_id", sa.String(64), nullable=False),
        sa.Column("event_type", sa.String(100), nullable=False),
        sa.Column("payload", postgresql.JSONB(), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
    )
    # The relay's query: oldest unpublished first. Partial, so it only ever holds the backlog.
    op.create_index(
        "ix_outbox_unpublished",
        "outbox",
        ["occurred_at", "id"],
        postgresql_where=sa.text("published_at IS NULL"),
    )
    # The pruner's query: published rows past the retention window.
    op.create_index(
        "ix_outbox_published_at",
        "outbox",
        ["published_at"],
        postgresql_where=sa.text("published_at IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index("ix_outbox_published_at", table_name="outbox")
    op.drop_index("ix_outbox_unpublished", table_name="outbox")
    op.drop_table("outbox")
