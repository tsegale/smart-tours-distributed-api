import enum
from datetime import datetime

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Integer,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .database import Base


class BookingStatus(enum.StrEnum):
    # PENDING_PAYMENT and EXPIRED come into use with the Stripe hold flow in Phase 1.
    PENDING_PAYMENT = "PENDING_PAYMENT"
    CONFIRMED = "CONFIRMED"
    CANCELLED = "CANCELLED"
    EXPIRED = "EXPIRED"


class TourAvailability(Base):
    """One departure of a tour (a date and time with a fixed number of seats)."""

    __tablename__ = "tour_availability"
    __table_args__ = (
        # The database is the last line of defence against overbooking: even a code path
        # that forgets the row lock cannot push booked_slots past total_slots.
        CheckConstraint("total_slots > 0", name="ck_availability_total_positive"),
        CheckConstraint("booked_slots >= 0 AND booked_slots <= total_slots", name="ck_availability_not_overbooked"),
        UniqueConstraint("tour_id", "starts_at", name="uq_availability_tour_start"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    # Owned by tour-core-service. There is deliberately no foreign key across service boundaries.
    tour_id: Mapped[int] = mapped_column(BigInteger, index=True)
    starts_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    total_slots: Mapped[int] = mapped_column(Integer)
    booked_slots: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    @property
    def slots_remaining(self) -> int:
        return self.total_slots - self.booked_slots


class Booking(Base):
    __tablename__ = "bookings"
    __table_args__ = (
        CheckConstraint("slots > 0", name="ck_bookings_slots_positive"),
        CheckConstraint(
            "status IN ('PENDING_PAYMENT', 'CONFIRMED', 'CANCELLED', 'EXPIRED')", name="ck_bookings_status"
        ),
        # A retried request with the same Idempotency-Key must never create a second booking.
        UniqueConstraint("user_id", "idempotency_key", name="uq_bookings_user_idempotency_key"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    user_id: Mapped[str] = mapped_column(String(255), index=True)
    availability_id: Mapped[int] = mapped_column(ForeignKey("tour_availability.id"), index=True)
    slots: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(20), default=BookingStatus.CONFIRMED)
    idempotency_key: Mapped[str | None] = mapped_column(String(255))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
    cancelled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    availability: Mapped[TourAvailability] = relationship(lazy="joined")
