"""Recording a booking event: three idempotency layers, all in one transaction (ADR 0014).

1. processed_events: the same eventId twice (at-least-once delivery) changes nothing the second time.
2. booking_snapshots: only a newer event (by occurredAt) updates the snapshot, so an overtaken event, such as a
   confirmation that arrives after the cancellation, sends nothing.
3. notifications (booking_id, type) unique: one confirmation per booking, whatever path tries to create a second.

The caller commits, then dispatches the Celery tasks for the notifications this returns (app.dispatch).
"""

import logging

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from .config import get_settings
from .events import BookingEvent
from .models import BookingSnapshot, Notification, NotificationStatus, NotificationType, ProcessedEvent

log = logging.getLogger(__name__)


def message_id(booking_id: int, reference: str, notification_type: NotificationType) -> str:
    """The same for every attempt at the same notification, so a retried send is recognisably the same mail."""
    return f"<{notification_type.lower()}.{booking_id}.{reference.lower()}@{get_settings().message_id_domain}>"


def record_booking_event(db: Session, event: BookingEvent) -> list[int]:
    """Apply one lifecycle event. Returns the ids of notifications it created (none for a duplicate)."""
    first_time = db.execute(
        insert(ProcessedEvent)
        .values(event_id=event.event_id, event_type=event.event_type)
        .on_conflict_do_nothing(index_elements=["event_id"])
        .returning(ProcessedEvent.event_id)
    ).first()
    if first_time is None:
        log.info("event.duplicate event_id=%s booking_id=%s", event.event_id, event.booking_id)
        return []

    if not _apply_to_snapshot(db, event):
        log.info(
            "event.overtaken event_id=%s booking_id=%s type=%s: a newer status is already recorded",
            event.event_id,
            event.booking_id,
            event.event_type,
        )
        return []

    if event.contact_email is None:
        log.info("notification.skipped booking_id=%s reason=no_contact_email", event.booking_id)
        return []
    created = create_notification(db, event.booking_id, event.reference, event.contact_email, event.notification_type,
                                  event.event_id)  # fmt: skip
    return [created] if created is not None else []


def create_notification(
    db: Session,
    booking_id: int,
    reference: str,
    recipient: str,
    notification_type: NotificationType,
    event_id=None,
) -> int | None:
    """Insert a PENDING notification unless this booking already has one of this type. Returns its id if new."""
    return db.execute(
        insert(Notification)
        .values(
            booking_id=booking_id,
            type=notification_type,
            status=NotificationStatus.PENDING,
            recipient=recipient,
            message_id=message_id(booking_id, reference, notification_type),
            event_id=event_id,
        )
        .on_conflict_do_nothing(constraint="uq_notifications_booking_type")
        .returning(Notification.id)
    ).scalar_one_or_none()


def _apply_to_snapshot(db: Session, event: BookingEvent) -> bool:
    """Upsert the snapshot if this event is newer than what it holds. True if it was applied."""
    departure = event.departure
    values = {
        "booking_id": event.booking_id,
        "reference": event.reference,
        "user_id": event.user_id,
        "contact_email": event.contact_email,
        "status": event.status,
        "status_changed_at": event.occurred_at,
        "slots": event.slots,
        "total_amount": str(event.total_amount),
        "currency": event.currency,
        "refund_amount": str(event.refund.amount) if event.refund else None,
        "availability_id": departure.availability_id,
        "tour_id": departure.tour_id,
        "tour_name": departure.tour_name,
        "meeting_point": departure.meeting_point,
        "time_zone": departure.time_zone,
        "starts_at": departure.starts_at,
    }
    statement = insert(BookingSnapshot).values(**values)
    applied = db.execute(
        statement.on_conflict_do_update(
            index_elements=["booking_id"],
            set_={key: statement.excluded[key] for key in values if key != "booking_id"},
            where=BookingSnapshot.status_changed_at < statement.excluded.status_changed_at,
        ).returning(BookingSnapshot.booking_id)
    ).first()
    return applied is not None


def snapshot(db: Session, booking_id: int) -> BookingSnapshot | None:
    return db.execute(select(BookingSnapshot).where(BookingSnapshot.booking_id == booking_id)).scalar_one_or_none()
