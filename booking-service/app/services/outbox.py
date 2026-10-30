"""Writes integration events to the transactional outbox (ADR 0008).

Call these inside the transaction that makes the change: the event row commits or rolls back
with it. Nothing here talks to RabbitMQ; app.jobs.outbox_relay does that afterwards.

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

from ..models import OutboxEvent, TourAvailability

AVAILABILITY_CHANGED = "availability.changed"
REVIEW_SUBMITTED = "review.submitted"


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
