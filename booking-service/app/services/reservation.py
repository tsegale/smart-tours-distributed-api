"""Three ways to take seats from a departure without overselling (ADR 0002).

The race being prevented: two requests both read "1 seat left", both pass the check, both
write, and the departure ends up oversold. Each strategy closes it differently:

- pessimistic: SELECT ... FOR UPDATE, then check and write. Contenders queue on the row lock.
- optimistic: read without a lock, then a compare-and-swap UPDATE on the version column.
  A lost race retries with jittered backoff and gives up with 409 after a bounded number of tries.
- atomic: one conditional UPDATE that checks capacity and increments in the same statement.
  The default: best median throughput and latency in docs/benchmarks/booking-locking.md.

reserve() runs inside the caller's transaction: on return the seats are taken but not
committed, so the caller can add the booking row and commit both together. Releasing seats
(cancel, expiry, late payment) always uses the pessimistic lock, because those paths also
lock the booking and must keep the availability -> booking lock order.

All of them bump `version`, so an optimistic compare-and-swap can never overwrite a change
made by another path.
"""

import logging
import random
import time
from typing import Protocol

from sqlalchemy import select, update
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session
from sqlalchemy.orm.attributes import set_committed_value

from ..config import Settings
from ..errors import ConflictError, NotEnoughSlotsError, NotFoundError
from ..models import TourAvailability

log = logging.getLogger(__name__)

LOCK_NOT_AVAILABLE = "55P03"  # Postgres SQLSTATE raised when lock_timeout expires


def _busy(exc: OperationalError) -> bool:
    return getattr(exc.orig, "sqlstate", None) == LOCK_NOT_AVAILABLE


def _not_found(availability_id: int) -> NotFoundError:
    return NotFoundError(f"Availability {availability_id} does not exist", availability_id=availability_id)


def _not_enough(requested: int, remaining: int) -> NotEnoughSlotsError:
    return NotEnoughSlotsError(f"Requested {requested} slots but only {remaining} remain", slots_remaining=remaining)


def lock_availability(db: Session, availability_id: int) -> TourAvailability:
    """SELECT ... FOR UPDATE on one departure. 409 (retryable) if the lock wait times out."""
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
        if _busy(exc):
            raise ConflictError("This departure is busy; please retry", retryable=True) from exc
        raise
    if availability is None:
        raise _not_found(availability_id)
    return availability


class SeatReservationStrategy(Protocol):
    name: str

    def reserve(self, db: Session, availability_id: int, slots: int) -> TourAvailability:
        """Take `slots` seats in the current transaction, or raise NotEnoughSlots/NotFound/Conflict."""
        ...


class PessimisticReservation:
    name = "pessimistic"

    def reserve(self, db: Session, availability_id: int, slots: int) -> TourAvailability:
        availability = lock_availability(db, availability_id)
        if slots > availability.slots_remaining:
            raise _not_enough(slots, availability.slots_remaining)
        availability.booked_slots += slots  # the ORM flush checks and bumps version
        db.flush()
        return availability


class OptimisticReservation:
    name = "optimistic"

    def __init__(self, max_attempts: int, backoff_base_ms: float, backoff_cap_ms: float) -> None:
        self._max_attempts = max_attempts
        self._base_s = backoff_base_ms / 1000
        self._cap_s = backoff_cap_ms / 1000

    def reserve(self, db: Session, availability_id: int, slots: int) -> TourAvailability:
        for attempt in range(1, self._max_attempts + 1):
            # Plain read: no lock. Under READ COMMITTED each statement sees the latest commit,
            # so a retry re-reads fresh values without having to roll back.
            availability = db.execute(
                select(TourAvailability)
                .where(TourAvailability.id == availability_id)
                .execution_options(populate_existing=True)
            ).scalar_one_or_none()
            if availability is None:
                raise _not_found(availability_id)
            if slots > availability.slots_remaining:
                raise _not_enough(slots, availability.slots_remaining)

            seen_version = availability.version
            try:
                swapped = db.execute(
                    update(TourAvailability)
                    .where(TourAvailability.id == availability_id, TourAvailability.version == seen_version)
                    .values(booked_slots=TourAvailability.booked_slots + slots, version=seen_version + 1)
                    .returning(TourAvailability.booked_slots, TourAvailability.version)
                    .execution_options(synchronize_session=False)
                ).one_or_none()
            except OperationalError as exc:
                if _busy(exc):
                    raise ConflictError("This departure is busy; please retry", retryable=True) from exc
                raise
            if swapped is not None:
                # Record the new values as loaded, not as changes: a dirty object would make the
                # next flush issue a second, version-checked UPDATE.
                set_committed_value(availability, "booked_slots", swapped.booked_slots)
                set_committed_value(availability, "version", swapped.version)
                return availability

            if attempt < self._max_attempts:
                # Full jitter (AWS architecture blog): spread retries out instead of retrying in lockstep.
                time.sleep(random.uniform(0, min(self._cap_s, self._base_s * 2**attempt)))
        log.info("optimistic reservation gave up on availability %s after %s attempts", availability_id, attempt)
        raise ConflictError("This departure is busy; please retry", retryable=True, attempts=self._max_attempts)


class AtomicReservation:
    name = "atomic"

    def reserve(self, db: Session, availability_id: int, slots: int) -> TourAvailability:
        try:
            row = db.execute(
                update(TourAvailability)
                .where(
                    TourAvailability.id == availability_id,
                    TourAvailability.booked_slots + slots <= TourAvailability.total_slots,
                )
                .values(booked_slots=TourAvailability.booked_slots + slots, version=TourAvailability.version + 1)
                .returning(TourAvailability.id)
                .execution_options(synchronize_session=False)
            ).one_or_none()
        except OperationalError as exc:
            if _busy(exc):
                raise ConflictError("This departure is busy; please retry", retryable=True) from exc
            raise
        # Re-read in the same transaction: our own UPDATE (if any) is visible, and the row is
        # locked by it, so the values are current.
        availability = db.execute(
            select(TourAvailability)
            .where(TourAvailability.id == availability_id)
            .execution_options(populate_existing=True)
        ).scalar_one_or_none()
        if availability is None:
            raise _not_found(availability_id)
        if row is None:
            raise _not_enough(slots, availability.slots_remaining)
        return availability


def strategy_from_settings(settings: Settings) -> SeatReservationStrategy:
    match settings.reservation_strategy:
        case "pessimistic":
            return PessimisticReservation()
        case "optimistic":
            return OptimisticReservation(
                settings.optimistic_max_attempts,
                settings.optimistic_backoff_base_ms,
                settings.optimistic_backoff_cap_ms,
            )
        case "atomic":
            return AtomicReservation()
    raise ValueError(f"Unknown reservation strategy {settings.reservation_strategy!r}")
