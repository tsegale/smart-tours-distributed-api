"""Expires holds nobody paid for, and retries refunds that failed.

Locking, per departure, in one transaction each:
    1. SELECT ... FOR UPDATE on the departure (availability first, as on every seat path).
    2. SELECT the overdue holds of that departure FOR UPDATE SKIP LOCKED, LIMIT batch.
    3. Mark them EXPIRED, give their seats back, commit.

Two sweepers can't double-release: the second one queues on the departure lock, and once it
has it, its fresh READ COMMITTED snapshot no longer sees the holds the first one expired.
SKIP LOCKED means a booking row that some other transaction holds is left for the next run
instead of stalling the batch.

The provider checkout expires at the same moment as the hold (see hold_ttl), so the sweeper
doesn't call the provider. A payment that still lands just after the sweep is handled as a
late payment by the webhook (ADR 0006).
"""

import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import select, true
from sqlalchemy.orm import Session

from ..domain.booking_status import transition
from ..errors import ConflictError
from ..models import Booking, BookingStatus, Payment, PaymentStatus
from ..payments.base import PaymentProvider
from .booking_service import issue_refund
from .reservation import lock_availability

log = logging.getLogger(__name__)

# A refund this fresh is probably still being issued inline by the request that decided it.
REFUND_RETRY_AFTER = timedelta(seconds=30)


@dataclass
class SweepResult:
    expired: int = 0
    seats_released: int = 0
    departures_skipped: int = 0


def expire_overdue_holds(
    session_factory: Callable[[], Session], batch_size: int, now: datetime | None = None
) -> SweepResult:
    now = now or datetime.now(UTC)
    result = SweepResult()
    skipped: set[int] = set()
    while True:
        with session_factory() as db:
            departures = list(
                db.execute(
                    select(Booking.availability_id)
                    .where(
                        Booking.status == BookingStatus.PENDING_PAYMENT,
                        Booking.hold_expires_at <= now,
                        Booking.availability_id.not_in(skipped) if skipped else true(),
                    )
                    .distinct()
                    .limit(batch_size)
                ).scalars()
            )
        if not departures:
            return result
        progress = False
        for availability_id in departures:
            with session_factory() as db:
                try:
                    expired, seats = _expire_departure(db, availability_id, now, batch_size)
                except ConflictError:
                    # Lock wait timed out on a busy departure: leave it for the next run.
                    skipped.add(availability_id)
                    result.departures_skipped += 1
                    continue
            result.expired += expired
            result.seats_released += seats
            progress = progress or expired > 0
            if expired < batch_size:
                skipped.add(availability_id)  # nothing left there (or only rows others hold)
        if not progress:
            return result


def _expire_departure(db: Session, availability_id: int, now: datetime, batch_size: int) -> tuple[int, int]:
    try:
        availability = lock_availability(db, availability_id)
        holds = list(
            db.execute(
                select(Booking)
                .where(
                    Booking.availability_id == availability_id,
                    Booking.status == BookingStatus.PENDING_PAYMENT,
                    Booking.hold_expires_at <= now,
                )
                .order_by(Booking.id)
                .limit(batch_size)
                .with_for_update(of=Booking, skip_locked=True)
                .execution_options(populate_existing=True)
            ).scalars()
        )
        released = 0
        for booking in holds:
            transition(booking, BookingStatus.EXPIRED, reason="hold_expired")
            released += booking.slots
        availability.booked_slots -= released
        if holds:
            db.execute(
                Payment.__table__.update()
                .where(
                    Payment.booking_id.in_([b.id for b in holds]),
                    Payment.status == PaymentStatus.PENDING,
                )
                .values(status=PaymentStatus.EXPIRED)
            )
        db.commit()
    except Exception:
        db.rollback()
        raise
    if holds:
        log.info("sweeper.expired departure=%s holds=%s seats=%s", availability_id, len(holds), released)
    return len(holds), released


def retry_pending_refunds(
    session_factory: Callable[[], Session], provider: PaymentProvider, batch_size: int, now: datetime | None = None
) -> int:
    """Re-issue refunds left REFUND_PENDING. Idempotent: the provider sees the same key each time."""
    cutoff = (now or datetime.now(UTC)) - REFUND_RETRY_AFTER
    with session_factory() as db:
        booking_ids = list(
            db.execute(
                select(Payment.booking_id)
                .where(Payment.status == PaymentStatus.REFUND_PENDING, Payment.updated_at <= cutoff)
                .order_by(Payment.updated_at)
                .limit(batch_size)
            ).scalars()
        )
    refunded = 0
    for booking_id in booking_ids:
        with session_factory() as db:
            if issue_refund(db, booking_id, provider) == PaymentStatus.REFUNDED:
                refunded += 1
    return refunded
