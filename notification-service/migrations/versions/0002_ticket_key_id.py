"""Record which key signed each ticket, so a retired key is unpublished only after its last ticket expires.

Revision ID: 0002
Revises: 0001
Create Date: 2026-11-04
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("tickets", sa.Column("key_id", sa.String(64), nullable=True))
    # The rotation runbook asks "when does the last ticket signed with this key expire?"
    op.create_index("ix_tickets_key_id_expires", "tickets", ["key_id", "token_expires_at"])


def downgrade() -> None:
    op.drop_index("ix_tickets_key_id_expires", table_name="tickets")
    op.drop_column("tickets", "key_id")
