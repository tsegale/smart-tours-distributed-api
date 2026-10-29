"""The booking state machine. Every status change goes through transition(), and the table
below is the only place that says which changes are legal.

    PENDING_PAYMENT --paid--------------------> CONFIRMED --cancel, early---> REFUNDED
          |  \\--hold lapsed / session expired-> EXPIRED      \\--cancel, late--> CANCELLED
          |                                       |
          \\--cancelled / payment failed           |  late payment (paid after the hold lapsed):
              / checkout could not start          |    seats still free -> CONFIRMED
              -> CANCELLED                        |    sold out         -> REFUNDED
                     |
                     \\--payment captured anyway -> REFUNDED

REFUNDED is terminal. EXPIRED and CANCELLED only move on when money arrives after the fact,
and then only to CONFIRMED (seats re-reserved) or REFUNDED (money returned). See ADR 0006.
"""

import logging
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from ..errors import IllegalTransitionError
from ..models import BookingStatus

if TYPE_CHECKING:
    from ..models import Booking

log = logging.getLogger("booking.transition")

S = BookingStatus

TRANSITIONS: dict[BookingStatus, frozenset[BookingStatus]] = {
    S.PENDING_PAYMENT: frozenset({S.CONFIRMED, S.EXPIRED, S.CANCELLED}),
    S.CONFIRMED: frozenset({S.CANCELLED, S.REFUNDED}),
    S.EXPIRED: frozenset({S.CONFIRMED, S.REFUNDED}),
    S.CANCELLED: frozenset({S.REFUNDED}),
    S.REFUNDED: frozenset(),
}

# Statuses whose seats are counted in tour_availability.booked_slots.
HOLDS_SEATS: frozenset[BookingStatus] = frozenset({S.PENDING_PAYMENT, S.CONFIRMED})


def can_transition(current: BookingStatus | str, target: BookingStatus) -> bool:
    return target in TRANSITIONS[BookingStatus(current)]


def ensure_can_transition(current: BookingStatus | str, target: BookingStatus, booking_id: int) -> None:
    """Raise a 409 Problem if the table does not allow current -> target."""
    if not can_transition(current, target):
        raise IllegalTransitionError(
            f"A {current} booking cannot become {target}",
            booking_id=booking_id,
            current_status=BookingStatus(current),
            requested_status=target,
        )


def transition(booking: "Booking", target: BookingStatus, reason: str) -> None:
    """Move a booking to `target` or raise a 409 Problem. The caller holds the booking's row lock."""
    current = BookingStatus(booking.status)
    ensure_can_transition(current, target, booking.id)
    now = datetime.now(UTC)
    booking.status = target
    if target == S.CONFIRMED:
        booking.confirmed_at = now
    if target in (S.CANCELLED, S.REFUNDED) and booking.cancelled_at is None:
        booking.cancelled_at = now
    log.info(
        "booking.transition booking_id=%s from=%s to=%s reason=%s",
        booking.id,
        current,
        target,
        reason,
        extra={"booking_id": booking.id, "from_status": current, "to_status": target, "reason": reason},
    )
