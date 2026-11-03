"""Booking lifecycle events as booking-service publishes them (docs/events/booking-lifecycle.schema.json)."""

import json
import uuid
from datetime import UTC, datetime, timedelta

STATUS = {
    "booking.confirmed": ("CONFIRMED", "PENDING_PAYMENT", "payment_succeeded"),
    "booking.cancelled": ("CANCELLED", "CONFIRMED", "customer_cancelled_no_refund"),
    "booking.refunded": ("REFUNDED", "CONFIRMED", "customer_cancelled_full_refund"),
    "booking.hold_expired": ("EXPIRED", "PENDING_PAYMENT", "hold_expired"),
}


def booking_event(
    event_type: str = "booking.confirmed",
    booking_id: int = 18,
    *,
    email: str | None = "alice@example.com",
    starts_at: datetime | None = None,
    occurred_at: datetime | None = None,
    time_zone: str = "Africa/Windhoek",
    event_id: uuid.UUID | None = None,
) -> dict:
    status, previous, reason = STATUS[event_type]
    starts_at = starts_at or datetime.now(UTC) + timedelta(days=10)
    event = {
        "eventId": str(event_id or uuid.uuid4()),
        "eventType": event_type,
        "occurredAt": (occurred_at or datetime.now(UTC)).isoformat(),
        "schemaVersion": 1,
        "bookingId": booking_id,
        "reference": f"ST-{booking_id:08d}",
        "userId": "alice",
        "contactEmail": email,
        "status": status,
        "previousStatus": previous,
        "reason": reason,
        "slots": 2,
        "unitPrice": "1850.00",
        "totalAmount": "3700.00",
        "currency": "NAD",
        "departure": {
            "availabilityId": 7,
            "tourId": 42,
            "tourName": "Sossusvlei Sunrise Dunes & Deadvlei",
            "meetingPoint": "Sesriem gate",
            "timeZone": time_zone,
            "startsAt": starts_at.astimezone(UTC).isoformat(),
        },
    }
    if event_type == "booking.refunded":
        event["refund"] = {"amount": "3700.00", "currency": "NAD"}
    if event_type == "booking.hold_expired":
        event["holdExpiresAt"] = datetime.now(UTC).isoformat()
    return event


def body(event: dict) -> bytes:
    return json.dumps(event).encode()
