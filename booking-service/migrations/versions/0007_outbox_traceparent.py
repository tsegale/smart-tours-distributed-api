"""Keep each outbox row's W3C trace context, so its trace continues through the relay (ADR 0017).

Revision ID: 0007
Revises: 0006
Create Date: 2026-11-05
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0007"
down_revision: str | None = "0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("outbox", sa.Column("traceparent", sa.String(55), nullable=True))


def downgrade() -> None:
    op.drop_column("outbox", "traceparent")
