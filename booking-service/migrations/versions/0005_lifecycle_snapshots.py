"""What notifications need, snapshotted so no consumer has to call back (Phase 4).

- tour_availability: the tour's meeting point and IANA time zone, copied from tour-core when the departure is
  opened, next to the name and price already copied then.
- bookings: the customer's contact email (from the token, via the gateway's X-User-Email; optional), and a
  booking reference to print on tickets and quote on the phone, unguessable rather than the row id.

Existing rows: departures get Africa/Windhoek (every tour so far is in Namibia or a UTC+2 neighbour), bookings a
generated reference.

Revision ID: 0005
Revises: 0004
Create Date: 2026-11-03
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0005"
down_revision: str | None = "0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("tour_availability", sa.Column("meeting_point", sa.String(500), nullable=True))
    op.add_column(
        "tour_availability",
        sa.Column("time_zone", sa.String(64), nullable=False, server_default="Africa/Windhoek"),
    )
    # New departures always carry the tour's own zone; the default was only for existing rows.
    op.alter_column("tour_availability", "time_zone", server_default=None)

    op.add_column("bookings", sa.Column("contact_email", sa.String(320), nullable=True))
    op.add_column("bookings", sa.Column("reference", sa.String(16), nullable=True))
    op.execute("UPDATE bookings SET reference = 'ST-' || upper(substr(md5(random()::text || id::text), 1, 8))")
    op.alter_column("bookings", "reference", nullable=False)
    op.create_unique_constraint("uq_bookings_reference", "bookings", ["reference"])


def downgrade() -> None:
    op.drop_constraint("uq_bookings_reference", "bookings", type_="unique")
    op.drop_column("bookings", "reference")
    op.drop_column("bookings", "contact_email")
    op.drop_column("tour_availability", "time_zone")
    op.drop_column("tour_availability", "meeting_point")
