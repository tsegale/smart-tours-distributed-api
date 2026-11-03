"""Hands committed notifications to Celery: a confirmation renders its ticket first, the rest go straight out.

Called after the transaction that created the notifications has committed, so a task never runs for a row that
rolled back. Enqueueing can still fail after that commit (the Celery broker is down): the notification then stays
PENDING, and the redispatch_pending Beat task enqueues it again once its dispatched_at is old enough. The tasks check
the notification's status first, so a notification dispatched twice is still sent once.
"""

import logging
from collections.abc import Callable, Sequence
from datetime import UTC, datetime

from celery import chain
from sqlalchemy import update
from sqlalchemy.orm import Session

from .models import Notification, NotificationType

log = logging.getLogger(__name__)


def dispatch(session_factory: Callable[[], Session], notification_ids: Sequence[int]) -> None:
    if not notification_ids:
        return
    from .tasks import render_ticket, send_email  # the task module imports this one

    # Marked and committed before enqueueing: a task must never find its row still locked by this transaction.
    with session_factory() as db:
        rows = db.execute(
            update(Notification)
            .where(Notification.id.in_(notification_ids))
            .values(dispatched_at=datetime.now(UTC))
            .returning(Notification.id, Notification.type, Notification.booking_id)
        ).all()
        db.commit()
    for notification_id, notification_type, booking_id in rows:
        if notification_type == NotificationType.CONFIRMATION:
            chain(render_ticket.si(notification_id), send_email.si(notification_id)).apply_async()
        else:
            send_email.delay(notification_id)
        log.info(
            "notification.dispatched notification_id=%s booking_id=%s type=%s",
            notification_id,
            booking_id,
            notification_type,
            extra={"notification_id": notification_id, "booking_id": booking_id},
        )
