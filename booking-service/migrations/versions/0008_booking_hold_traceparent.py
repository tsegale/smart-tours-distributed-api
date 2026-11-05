"""Keep the trace of the request that took each hold, so the payment's trace can link back to it (ADR 0017).

Revision ID: 0008
Revises: 0007
Create Date: 2026-11-05
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0008"
down_revision: str | None = "0007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("bookings", sa.Column("hold_traceparent", sa.String(55), nullable=True))


def downgrade() -> None:
    op.drop_column("bookings", "hold_traceparent")
