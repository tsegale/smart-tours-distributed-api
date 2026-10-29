"""Seat inventory row lock (ADR 0002).

The race being prevented: two requests both read "1 seat left", both pass the check, both
write, and the departure ends up oversold. Every path that changes booked_slots takes a row
lock on the departure first (SELECT ... FOR UPDATE), always before locking a booking, so the
lock order is availability -> booking everywhere and no two paths can deadlock.
"""

from sqlalchemy import select
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from ..errors import ConflictError, NotFoundError
from ..models import TourAvailability

LOCK_NOT_AVAILABLE = "55P03"  # Postgres SQLSTATE raised when lock_timeout expires


def _busy(exc: OperationalError) -> bool:
    return getattr(exc.orig, "sqlstate", None) == LOCK_NOT_AVAILABLE


def _not_found(availability_id: int) -> NotFoundError:
    return NotFoundError(f"Availability {availability_id} does not exist", availability_id=availability_id)


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
