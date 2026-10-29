import enum
from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .database import Base


class BookingStatus(enum.StrEnum):
    """Allowed moves between these live in app.domain.booking_status, nowhere else."""

    PENDING_PAYMENT = "PENDING_PAYMENT"
    CONFIRMED = "CONFIRMED"
    CANCELLED = "CANCELLED"
    EXPIRED = "EXPIRED"
    REFUNDED = "REFUNDED"


class PaymentStatus(enum.StrEnum):
    PENDING = "PENDING"  # checkout session open, customer has not paid yet
    PAID = "PAID"
    FAILED = "FAILED"  # async payment method declined
    EXPIRED = "EXPIRED"  # session expired or was expired by a cancellation
    REFUND_PENDING = "REFUND_PENDING"  # refund decided and committed, provider call outstanding
    REFUNDED = "REFUNDED"


class TourAvailability(Base):
    """One departure of a tour (a date and time with a fixed number of seats).

    booked_slots counts held (PENDING_PAYMENT) and confirmed seats alike: a seat on hold is not for sale.
    """

    __tablename__ = "tour_availability"
    __table_args__ = (
        # The database is the last line of defence against overbooking: even a code path
        # that forgets the row lock cannot push booked_slots past total_slots.
        CheckConstraint("total_slots > 0", name="ck_availability_total_positive"),
        CheckConstraint("booked_slots >= 0 AND booked_slots <= total_slots", name="ck_availability_not_overbooked"),
        CheckConstraint("unit_price >= 0", name="ck_availability_price_non_negative"),
        UniqueConstraint("tour_id", "starts_at", name="uq_availability_tour_start"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    # Owned by tour-core-service. There is deliberately no foreign key across service boundaries.
    tour_id: Mapped[int] = mapped_column(BigInteger, index=True)
    # Snapshot of the tour's name and price when the departure was opened.
    tour_name: Mapped[str] = mapped_column(String(255))
    unit_price: Mapped[Decimal] = mapped_column(Numeric(10, 2))
    currency: Mapped[str] = mapped_column(String(3))
    starts_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    total_slots: Mapped[int] = mapped_column(Integer)
    booked_slots: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    # Optimistic-concurrency token. Every ORM UPDATE of this row checks and bumps it (version_id_col),
    # and the Core UPDATEs in app.services.reservation bump it explicitly, so a compare-and-swap
    # never overwrites a change made by a locking path.
    version: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    __mapper_args__ = {"version_id_col": version}

    @property
    def slots_remaining(self) -> int:
        return self.total_slots - self.booked_slots


class Booking(Base):
    __tablename__ = "bookings"
    __table_args__ = (
        CheckConstraint("slots > 0", name="ck_bookings_slots_positive"),
        CheckConstraint(
            "status IN ('PENDING_PAYMENT', 'CONFIRMED', 'CANCELLED', 'EXPIRED', 'REFUNDED')",
            name="ck_bookings_status",
        ),
        CheckConstraint("status <> 'PENDING_PAYMENT' OR hold_expires_at IS NOT NULL", name="ck_bookings_hold_set"),
        CheckConstraint("total_amount = unit_price * slots", name="ck_bookings_total"),
        # A retried request with the same Idempotency-Key must never create a second booking.
        UniqueConstraint("user_id", "idempotency_key", name="uq_bookings_user_idempotency_key"),
        # The sweeper's query: overdue holds only, so the index stays small.
        Index(
            "ix_bookings_hold_expiry",
            "hold_expires_at",
            postgresql_where=text("status = 'PENDING_PAYMENT'"),
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    user_id: Mapped[str] = mapped_column(String(255), index=True)
    availability_id: Mapped[int] = mapped_column(ForeignKey("tour_availability.id"), index=True)
    slots: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(20))
    # Price at booking time. Never recalculated, even if the tour price changes later.
    unit_price: Mapped[Decimal] = mapped_column(Numeric(10, 2))
    total_amount: Mapped[Decimal] = mapped_column(Numeric(10, 2))
    currency: Mapped[str] = mapped_column(String(3))
    idempotency_key: Mapped[str | None] = mapped_column(String(255))
    hold_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    cancelled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    availability: Mapped[TourAvailability] = relationship(lazy="joined")
    payment: Mapped["Payment | None"] = relationship(back_populates="booking", lazy="selectin")


class Payment(Base):
    """One provider checkout per booking, and what became of the money."""

    __tablename__ = "payments"
    __table_args__ = (
        CheckConstraint("amount_minor > 0", name="ck_payments_amount_positive"),
        UniqueConstraint("booking_id", name="uq_payments_booking_id"),
        UniqueConstraint("provider_checkout_id", name="uq_payments_provider_checkout_id"),
        CheckConstraint(
            "status IN ('PENDING', 'PAID', 'FAILED', 'EXPIRED', 'REFUND_PENDING', 'REFUNDED')",
            name="ck_payments_status",
        ),
        Index(
            "ix_payments_refund_pending",
            "updated_at",
            postgresql_where=text("status = 'REFUND_PENDING'"),
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    booking_id: Mapped[int] = mapped_column(ForeignKey("bookings.id"))
    provider: Mapped[str] = mapped_column(String(20))
    provider_checkout_id: Mapped[str] = mapped_column(String(255))
    provider_payment_id: Mapped[str | None] = mapped_column(String(255))
    provider_refund_id: Mapped[str | None] = mapped_column(String(255))
    checkout_url: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(20))
    # Integer minor units (cents), as payment providers expect. Never a float.
    amount_minor: Mapped[int] = mapped_column(BigInteger)
    currency: Mapped[str] = mapped_column(String(3))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    booking: Mapped[Booking] = relationship(back_populates="payment")


class ProcessedWebhookEvent(Base):
    """Provider event ids already applied. Inserted in the same transaction as the state change."""

    __tablename__ = "processed_webhook_events"

    event_id: Mapped[str] = mapped_column(String(255), primary_key=True)
    provider: Mapped[str] = mapped_column(String(20))
    event_type: Mapped[str] = mapped_column(String(100))
    processed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
