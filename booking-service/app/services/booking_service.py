"""Booking rules and concurrency control.

The race being prevented: two requests both read "1 seat left", both pass the check, both
write, and the departure ends up oversold. Every path that changes booked_slots therefore
takes a row lock on the availability row first (SELECT ... FOR UPDATE). Concurrent bookings
for the same departure queue up and run one at a time, while bookings for different
departures don't block each other.

Lock order is always availability -> booking, on both create and cancel, so the two can't
deadlock.
"""

from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError, OperationalError
from sqlalchemy.orm import Session

from ..errors import (
    ConflictError,
    IdempotencyKeyReusedError,
    NotEnoughSlotsError,
    NotFoundError,
)
from ..models import Booking, BookingStatus, TourAvailability
from ..schemas import AvailabilityCreate, BookingCreate

LOCK_NOT_AVAILABLE = "55P03"  # Postgres SQLSTATE raised when lock_timeout expires


def _lock_availability(db: Session, availability_id: int) -> TourAvailability:
    stmt = (
        select(TourAvailability)
        .where(TourAvailability.id == availability_id)
        .with_for_update()
        # Overwrite any copy already in the session: values read before the lock may be stale.
        .execution_options(populate_existing=True)
    )
    try:
        availability = db.execute(stmt).scalar_one_or_none()
    except OperationalError as exc:
        if getattr(exc.orig, "sqlstate", None) == LOCK_NOT_AVAILABLE:
            raise ConflictError("This departure is busy; please retry", retryable=True) from exc
        raise
    if availability is None:
        raise NotFoundError(f"Availability {availability_id} does not exist", availability_id=availability_id)
    return availability


def create_availability(db: Session, data: AvailabilityCreate) -> TourAvailability:
    availability = TourAvailability(tour_id=data.tour_id, starts_at=data.starts_at, total_slots=data.total_slots)
    db.add(availability)
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise ConflictError("This tour already has a departure at that time") from exc
    return availability


def list_availability(db: Session, tour_id: int) -> list[TourAvailability]:
    stmt = select(TourAvailability).where(TourAvailability.tour_id == tour_id).order_by(TourAvailability.starts_at)
    return list(db.execute(stmt).scalars())


def create_booking(db: Session, user_id: str, data: BookingCreate, idempotency_key: str | None) -> tuple[Booking, bool]:
    """Book seats atomically. Returns (booking, created); created is False on an idempotent replay."""
    try:
        availability = _lock_availability(db, data.availability_id)

        # Checked *after* taking the lock. A retry that raced its own first attempt therefore
        # waits for that attempt to commit, then finds the booking instead of creating another.
        if idempotency_key:
            existing = db.execute(
                select(Booking).where(Booking.user_id == user_id, Booking.idempotency_key == idempotency_key)
            ).scalar_one_or_none()
            if existing is not None:
                if existing.availability_id != data.availability_id or existing.slots != data.slots:
                    raise IdempotencyKeyReusedError(
                        "This Idempotency-Key was already used for a different booking request"
                    )
                db.rollback()
                return existing, False

        if data.slots > availability.slots_remaining:
            raise NotEnoughSlotsError(
                f"Requested {data.slots} slots but only {availability.slots_remaining} remain",
                slots_remaining=availability.slots_remaining,
            )

        availability.booked_slots += data.slots
        booking = Booking(
            user_id=user_id,
            availability_id=availability.id,
            slots=data.slots,
            status=BookingStatus.CONFIRMED,  # becomes PENDING_PAYMENT when Stripe lands (Phase 1)
            idempotency_key=idempotency_key,
        )
        db.add(booking)
        db.commit()  # releases the row lock
        db.refresh(booking)
        return booking, True
    except IntegrityError as exc:
        db.rollback()
        # Same key used concurrently for two different departures: the unique constraint is the backstop.
        raise IdempotencyKeyReusedError("This Idempotency-Key is already in use") from exc
    except Exception:
        db.rollback()
        raise


def get_booking_for_user(db: Session, user_id: str, booking_id: int) -> Booking:
    booking = db.get(Booking, booking_id)
    # Another user's booking gets the same 404 as a missing one, so ids can't be probed.
    if booking is None or booking.user_id != user_id:
        raise NotFoundError(f"Booking {booking_id} does not exist", booking_id=booking_id)
    return booking


def list_bookings_for_user(db: Session, user_id: str) -> list[Booking]:
    stmt = select(Booking).where(Booking.user_id == user_id).order_by(Booking.created_at.desc())
    return list(db.execute(stmt).scalars())


def cancel_booking(db: Session, user_id: str, booking_id: int) -> Booking:
    """Cancel a booking and return its seats. Cancelling twice is a no-op, not an error."""
    try:
        booking = get_booking_for_user(db, user_id, booking_id)
        availability = _lock_availability(db, booking.availability_id)  # lock order: availability first
        # Re-read the booking under a lock. of=Booking: Postgres can't lock the nullable side
        # of the outer join that eager-loads the availability.
        booking = db.execute(
            select(Booking)
            .where(Booking.id == booking.id)
            .with_for_update(of=Booking)
            .execution_options(populate_existing=True)
        ).scalar_one()

        if booking.status == BookingStatus.CANCELLED:
            db.rollback()
            return booking
        if booking.status not in (BookingStatus.CONFIRMED, BookingStatus.PENDING_PAYMENT):
            raise ConflictError(f"A booking in status {booking.status} cannot be cancelled")

        availability.booked_slots -= booking.slots
        booking.status = BookingStatus.CANCELLED
        booking.cancelled_at = datetime.now(UTC)
        db.commit()
        db.refresh(booking)
        return booking
    except Exception:
        db.rollback()
        raise
