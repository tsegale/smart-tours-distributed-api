"""Payment holds: price snapshots, REFUNDED status, payments, webhook dedupe, optimistic version.

Rows that predate this revision were booked without a price, so their snapshot columns are
backfilled with 0.00 NAD (the base currency) and the tour name with a placeholder. The
temporary server defaults are dropped again, so new rows must always carry a real snapshot.

Revision ID: 0002
Revises: 0001
Create Date: 2026-10-29
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

BACKFILL = {"tour_name": "(unknown tour)", "unit_price": "0.00", "currency": "NAD"}


def _add_snapshot_columns(table: str, columns: list[sa.Column]) -> None:
    for column in columns:
        op.add_column(table, column)
    for column in columns:
        op.alter_column(table, column.name, server_default=None)


def upgrade() -> None:
    _add_snapshot_columns(
        "tour_availability",
        [
            sa.Column("tour_name", sa.String(255), nullable=False, server_default=BACKFILL["tour_name"]),
            sa.Column("unit_price", sa.Numeric(10, 2), nullable=False, server_default=BACKFILL["unit_price"]),
            sa.Column("currency", sa.String(3), nullable=False, server_default=BACKFILL["currency"]),
        ],
    )
    op.add_column("tour_availability", sa.Column("version", sa.Integer(), nullable=False, server_default="0"))
    op.create_check_constraint("ck_availability_price_non_negative", "tour_availability", "unit_price >= 0")

    # Legacy bookings take the backfilled price of their departure, so total = unit_price * slots holds.
    _add_snapshot_columns(
        "bookings",
        [
            sa.Column("unit_price", sa.Numeric(10, 2), nullable=False, server_default=BACKFILL["unit_price"]),
            sa.Column("total_amount", sa.Numeric(10, 2), nullable=False, server_default=BACKFILL["unit_price"]),
            sa.Column("currency", sa.String(3), nullable=False, server_default=BACKFILL["currency"]),
        ],
    )
    op.add_column("bookings", sa.Column("hold_expires_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("bookings", sa.Column("confirmed_at", sa.DateTime(timezone=True), nullable=True))
    op.execute("UPDATE bookings SET confirmed_at = created_at WHERE status = 'CONFIRMED'")
    # Status is always set explicitly through the state machine now.
    op.alter_column("bookings", "status", server_default=None)

    op.drop_constraint("ck_bookings_status", "bookings", type_="check")
    op.create_check_constraint(
        "ck_bookings_status",
        "bookings",
        "status IN ('PENDING_PAYMENT', 'CONFIRMED', 'CANCELLED', 'EXPIRED', 'REFUNDED')",
    )
    op.create_check_constraint(
        "ck_bookings_hold_set", "bookings", "status <> 'PENDING_PAYMENT' OR hold_expires_at IS NOT NULL"
    )
    op.create_check_constraint("ck_bookings_total", "bookings", "total_amount = unit_price * slots")
    op.create_index(
        "ix_bookings_hold_expiry",
        "bookings",
        ["hold_expires_at"],
        postgresql_where=sa.text("status = 'PENDING_PAYMENT'"),
    )

    op.create_table(
        "payments",
        sa.Column("id", sa.BigInteger(), sa.Identity(), primary_key=True),
        sa.Column("booking_id", sa.BigInteger(), sa.ForeignKey("bookings.id"), nullable=False),
        sa.Column("provider", sa.String(20), nullable=False),
        sa.Column("provider_checkout_id", sa.String(255), nullable=False),
        sa.Column("provider_payment_id", sa.String(255), nullable=True),
        sa.Column("provider_refund_id", sa.String(255), nullable=True),
        sa.Column("checkout_url", sa.Text(), nullable=True),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("amount_minor", sa.BigInteger(), nullable=False),
        sa.Column("currency", sa.String(3), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint("amount_minor > 0", name="ck_payments_amount_positive"),
        sa.CheckConstraint(
            "status IN ('PENDING', 'PAID', 'FAILED', 'EXPIRED', 'REFUND_PENDING', 'REFUNDED')",
            name="ck_payments_status",
        ),
        sa.UniqueConstraint("booking_id", name="uq_payments_booking_id"),
        sa.UniqueConstraint("provider_checkout_id", name="uq_payments_provider_checkout_id"),
    )
    op.create_index(
        "ix_payments_refund_pending",
        "payments",
        ["updated_at"],
        postgresql_where=sa.text("status = 'REFUND_PENDING'"),
    )

    op.create_table(
        "processed_webhook_events",
        sa.Column("event_id", sa.String(255), primary_key=True),
        sa.Column("provider", sa.String(20), nullable=False),
        sa.Column("event_type", sa.String(100), nullable=False),
        sa.Column("processed_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )


def downgrade() -> None:
    op.drop_table("processed_webhook_events")
    op.drop_index("ix_payments_refund_pending", table_name="payments")
    op.drop_table("payments")

    op.drop_index("ix_bookings_hold_expiry", table_name="bookings")
    op.drop_constraint("ck_bookings_total", "bookings", type_="check")
    op.drop_constraint("ck_bookings_hold_set", "bookings", type_="check")
    op.drop_constraint("ck_bookings_status", "bookings", type_="check")
    # REFUNDED has no equivalent in 0001; a refunded booking was, in effect, cancelled.
    op.execute("UPDATE bookings SET status = 'CANCELLED' WHERE status = 'REFUNDED'")
    op.create_check_constraint(
        "ck_bookings_status", "bookings", "status IN ('PENDING_PAYMENT', 'CONFIRMED', 'CANCELLED', 'EXPIRED')"
    )
    op.alter_column("bookings", "status", server_default="CONFIRMED")
    for column in ("confirmed_at", "hold_expires_at", "currency", "total_amount", "unit_price"):
        op.drop_column("bookings", column)

    op.drop_constraint("ck_availability_price_non_negative", "tour_availability", type_="check")
    for column in ("version", "currency", "unit_price", "tour_name"):
        op.drop_column("tour_availability", column)
