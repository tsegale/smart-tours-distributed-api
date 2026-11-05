"""Writes integration events to the transactional outbox (ADR 0008).

Call these inside the transaction that makes the change: the event row commits or rolls back
with it. Nothing here talks to RabbitMQ; app.jobs.outbox_relay does that afterwards.

Every booking status change (transition()) is followed by booking_status_changed(): booking.confirmed,
booking.cancelled, booking.refunded and booking.hold_expired, each carrying everything a notifier needs, so
no consumer calls back. Schemas: docs/events.md; tests/test_event_contracts.py checks them.

Every path that changes a departure's seat count calls availability_changed() before it commits:
the three reservation strategies (via hold_seats), every release (cancel, compensation, webhooks,
sweeper) and the late-payment re-reserve. tests/conftest.py checks this after every test: the
newest availability.changed event of each departure must carry its current version and seat count.

Payloads are camelCase JSON, like the events tour-core publishes, and each one carries its eventId.
"""

import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy.orm import Session

from ..domain.booking_status import StatusChange
from ..models import Booking, BookingStatus, OutboxEvent, TourAvailability
from ..telemetry import current_traceparent

AVAILABILITY_CHANGED = "availability.changed"
REVIEW_SUBMITTED = "review.submitted"

# The routing key for each status a booking can move to. PENDING_PAYMENT is where bookings start, not a change.
BOOKING_EVENTS: dict[BookingStatus, str] = {
    BookingStatus.CONFIRMED: "booking.confirmed",
    BookingStatus.CANCELLED: "booking.cancelled",
    BookingStatus.REFUNDED: "booking.refunded",
    BookingStatus.EXPIRED: "booking.hold_expired",
}
# Bumped on a breaking change to the lifecycle payloads; additive changes keep it (docs/events.md).
BOOKING_SCHEMA_VERSION = 1


def record(
    db: Session, *, aggregate_type: str, aggregate_id: object, event_type: str, data: dict[str, Any]
) -> uuid.UUID:
    event_id = uuid.uuid4()
    occurred_at = datetime.now(UTC)
    payload = {"eventId": str(event_id), "eventType": event_type, "occurredAt": occurred_at.isoformat(), **data}
    db.add(
        OutboxEvent(
            id=event_id,
            aggregate_type=aggregate_type,
            aggregate_id=str(aggregate_id),
            event_type=event_type,
            payload=payload,
            occurred_at=occurred_at,
            traceparent=current_traceparent(),
        )
    )
    return event_id


def availability_changed(db: Session, availability: TourAvailability) -> uuid.UUID:
    """The departure's seat count changed in this transaction: publish its new state and version.

    Flushes first, because the ORM bumps `version` only when it writes the row, and the event must
    carry the version that commits. Several changes in one transaction give one event with the final
    state; consumers apply an event only if its version is newer than what they have.
    """
    db.flush()
    return record(
        db,
        aggregate_type="availability",
        aggregate_id=availability.id,
        event_type=AVAILABILITY_CHANGED,
        data={
            "availabilityId": availability.id,
            "tourId": availability.tour_id,
            "startsAt": availability.starts_at.astimezone(UTC).isoformat(),
            "totalSlots": availability.total_slots,
            "slotsLeft": availability.total_slots - availability.booked_slots,
            "version": availability.version,
        },
    )


def booking_status_changed(db: Session, booking: Booking, change: StatusChange) -> uuid.UUID:
    """The booking moved to a new status in this transaction: publish it with the departure's snapshot."""
    availability = booking.availability
    data: dict[str, Any] = {
        "schemaVersion": BOOKING_SCHEMA_VERSION,
        "bookingId": booking.id,
        "reference": booking.reference,
        "userId": booking.user_id,
        "contactEmail": booking.contact_email,
        "status": change.to_status.value,
        "previousStatus": change.from_status.value,
        "reason": change.reason,
        "slots": booking.slots,
        "unitPrice": str(booking.unit_price),
        "totalAmount": str(booking.total_amount),
        "currency": booking.currency,
        "departure": {
            "availabilityId": availability.id,
            "tourId": availability.tour_id,
            "tourName": availability.tour_name,
            "meetingPoint": availability.meeting_point,
            "timeZone": availability.time_zone,
            "startsAt": availability.starts_at.astimezone(UTC).isoformat(),
        },
    }
    if change.to_status == BookingStatus.REFUNDED:
        # Every refund is of the full amount (cancellation policy, ADR 0006).
        data["refund"] = {"amount": str(booking.total_amount), "currency": booking.currency}
    if change.to_status == BookingStatus.EXPIRED and booking.hold_expires_at is not None:
        data["holdExpiresAt"] = booking.hold_expires_at.astimezone(UTC).isoformat()
    return record(
        db,
        aggregate_type="booking",
        aggregate_id=booking.id,
        event_type=BOOKING_EVENTS[change.to_status],
        data=data,
    )
