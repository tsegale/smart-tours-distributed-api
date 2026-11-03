"""The booking lifecycle events this service consumes (docs/events.md).

Parsed strictly enough to refuse what it can't act on (a poison message goes straight to the dead-letter queue),
loosely enough to accept additive changes: unknown fields are ignored, as the compatibility rules ask.
"""

import json
import uuid
from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field, ValidationError
from pydantic.alias_generators import to_camel

from .models import NotificationType

SUPPORTED_SCHEMA_VERSION = 1

# What each lifecycle event owes the customer.
NOTIFICATION_FOR: dict[str, NotificationType] = {
    "booking.confirmed": NotificationType.CONFIRMATION,
    "booking.cancelled": NotificationType.CANCELLATION,
    "booking.refunded": NotificationType.REFUND,
    "booking.hold_expired": NotificationType.HOLD_EXPIRED,
}
ROUTING_KEYS = tuple(NOTIFICATION_FOR)


class PoisonMessageError(Exception):
    """The message can never be processed (malformed, unknown type or version): dead-letter it, don't retry."""


class _Model(BaseModel):
    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True, frozen=True, extra="ignore")


class Departure(_Model):
    availability_id: int
    tour_id: int
    tour_name: str
    meeting_point: str | None = None
    time_zone: str
    starts_at: datetime


class Refund(_Model):
    amount: Decimal
    currency: str


class BookingEvent(_Model):
    event_id: uuid.UUID
    event_type: str
    occurred_at: datetime
    schema_version: int
    booking_id: int
    reference: str
    user_id: str
    contact_email: str | None = None
    status: str
    previous_status: str
    reason: str
    slots: int = Field(gt=0)
    total_amount: Decimal
    currency: str
    departure: Departure
    refund: Refund | None = None

    @property
    def notification_type(self) -> NotificationType:
        return NOTIFICATION_FOR[self.event_type]


def parse_booking_event(body: bytes) -> BookingEvent:
    try:
        event = BookingEvent.model_validate(json.loads(body))
    except (ValueError, ValidationError) as exc:
        raise PoisonMessageError(f"unreadable booking event: {exc}") from exc
    if event.event_type not in NOTIFICATION_FOR:
        raise PoisonMessageError(f"not a booking lifecycle event: {event.event_type}")
    if event.schema_version != SUPPORTED_SCHEMA_VERSION:
        raise PoisonMessageError(f"{event.event_type} schemaVersion {event.schema_version} is not supported")
    return event
