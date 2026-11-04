"""The Celery tasks: render a ticket, send an email, and three Beat jobs (reminders, re-dispatch, outbox relay).

Every task is safe to run twice. A task message can be delivered again (acks_late), a notification can be
dispatched again (redispatch_pending), and two Beat instances can overlap; what stops a second email is the
notification's status, checked under a row lock right before sending, and the (booking_id, type) constraint.
"""

import logging
import random
from datetime import UTC, datetime, timedelta
from functools import lru_cache

from botocore.exceptions import BotoCoreError, ClientError
from celery import Task
from celery.exceptions import Reject
from sqlalchemy import or_, select
from sqlalchemy.dialects.postgresql import insert

from . import emails, outbox, storage, tickets
from .celery_app import app
from .config import get_settings
from .database import SessionLocal
from .dispatch import dispatch
from .emails import PermanentEmailError, SmtpEmailSender, TransientEmailError
from .intake import create_notification, snapshot
from .models import BookingSnapshot, Notification, NotificationStatus, NotificationType, Ticket

log = logging.getLogger("notifications.tasks")
settings = get_settings()

# Which booking status a notification still makes sense for, when its turn comes.
STILL_TRUE_WHEN: dict[NotificationType, str] = {
    NotificationType.CONFIRMATION: "CONFIRMED",
    NotificationType.CANCELLATION: "CANCELLED",
    NotificationType.REFUND: "REFUNDED",
    NotificationType.HOLD_EXPIRED: "EXPIRED",
    NotificationType.REMINDER: "CONFIRMED",
}


class NotificationTask(Task):
    """Marks the notification FAILED when a task crashes outright (a bug, not a retryable error)."""

    def on_failure(self, exc, task_id, args, kwargs, einfo) -> None:
        if args and isinstance(args[0], int):
            mark_failed(args[0], f"{type(exc).__name__}: {exc}")
        log.error("task.crashed notification_id=%s", args[0] if args else None, exc_info=einfo.exc_info)


def backoff(retries: int) -> float:
    """Exponential, capped, with full jitter: retries from many workers don't arrive in lockstep."""
    ceiling = min(settings.task_retry_backoff_max_s, settings.task_retry_backoff_s * 2**retries)
    return random.uniform(ceiling / 2, ceiling)


def retry_or_fail(task: Task, notification_id: int, exc: Exception):
    """Retry a transient failure later, or, once max_retries is spent, fail it into the dead-letter queue."""
    if task.request.retries >= task.max_retries:
        mark_failed(notification_id, f"retries exhausted: {exc}")
        log.error("notification.failed notification_id=%s reason=retries_exhausted", notification_id,
                  extra={"notification_id": notification_id})  # fmt: skip
        raise Reject(f"retries exhausted: {exc}", requeue=False) from exc
    record_attempt(notification_id, exc)
    countdown = backoff(task.request.retries)
    log.warning("notification.retry notification_id=%s in=%.0fs error=%s", notification_id, countdown, exc,
                extra={"notification_id": notification_id})  # fmt: skip
    raise task.retry(exc=exc, countdown=countdown)


def record_attempt(notification_id: int, exc: Exception) -> None:
    with SessionLocal() as db:
        notification = db.get(Notification, notification_id)
        if notification is not None:
            notification.attempts += 1
            notification.last_error = str(exc)[:2000]
            db.commit()


def mark_failed(notification_id: int, error: str) -> None:
    with SessionLocal() as db:
        notification = db.get(Notification, notification_id, with_for_update=True)
        if notification is not None and notification.status == NotificationStatus.PENDING:
            notification.status = NotificationStatus.FAILED
            notification.attempts += 1
            notification.last_error = error[:2000]
        db.commit()


@lru_cache
def tickets_bucket_ready() -> str:
    storage.ensure_bucket(storage.s3_client(), settings.tickets_bucket)
    return settings.tickets_bucket


def email_sender() -> emails.EmailSender:
    return SmtpEmailSender(settings)


# ---------------------------------------------------------------- tickets


@app.task(bind=True, base=NotificationTask, name="notifications.render_ticket", max_retries=settings.task_max_retries)
def render_ticket(self: Task, notification_id: int) -> None:
    with SessionLocal() as db:
        notification = db.get(Notification, notification_id)
        if notification is None or notification.status != NotificationStatus.PENDING:
            return
        booking = snapshot(db, notification.booking_id)
        if booking is None or booking.status != "CONFIRMED" or db.get(Ticket, notification.booking_id) is not None:
            return  # nothing to render (send_email decides what to do), or rendered already
    started = datetime.now(UTC)
    try:
        key = tickets.signing_key(settings.ticket_signing_key_file)
        tickets.ensure_published(key, settings.ticket_public_keys_dir)  # a crash, not a retry: it needs an operator
        token = tickets.issue_token(booking, key, timedelta(hours=settings.ticket_token_grace_hours))
        pdf = tickets.render_pdf(booking, token)
        digest = tickets.sha256_hex(pdf)
        key = tickets.object_key(booking)
        bucket = tickets_bucket_ready()
        storage.put_ticket(storage.s3_client(), bucket, key, pdf, digest)
    except (BotoCoreError, ClientError, OSError) as exc:
        retry_or_fail(self, notification_id, exc)
    with SessionLocal() as db:
        recorded = db.execute(
            insert(Ticket)
            .values(
                booking_id=booking.booking_id,
                reference=booking.reference,
                bucket=bucket,
                object_key=key,
                size_bytes=len(pdf),
                sha256=digest,
                token_expires_at=token.expires_at,
                key_id=token.key_id,
            )
            .on_conflict_do_nothing(index_elements=["booking_id"])
            .returning(Ticket.booking_id)
        ).first()
        if recorded is not None:
            outbox.ticket_rendered(db, db.get(Ticket, booking.booking_id))
        db.commit()
    log.info(
        "ticket.rendered booking_id=%s bytes=%s ms=%.0f",
        booking.booking_id,
        len(pdf),
        (datetime.now(UTC) - started).total_seconds() * 1000,
        extra={"booking_id": booking.booking_id, "notification_id": notification_id},
    )
    relay_outbox.delay()


# ---------------------------------------------------------------- email


@app.task(
    bind=True,
    base=NotificationTask,
    name="notifications.send_email",
    max_retries=settings.task_max_retries,
    rate_limit=settings.send_rate_limit,
)
def send_email(self: Task, notification_id: int) -> None:
    with SessionLocal() as db:
        # Locked while sending: a second copy of this task (a redelivery or a re-dispatch) waits here, at most the
        # SMTP timeout, then finds it SENT. That is the "never twice" check. Not SKIP LOCKED: a copy that skipped a
        # locked row would drop the notification if the lock holder turned out to be something else.
        notification = db.execute(
            select(Notification).where(Notification.id == notification_id).with_for_update()
        ).scalar_one_or_none()
        if notification is None or notification.status != NotificationStatus.PENDING:
            db.rollback()
            return
        booking = snapshot(db, notification.booking_id)
        expected = STILL_TRUE_WHEN[NotificationType(notification.type)]
        if (
            booking is None
            or booking.status != expected
            or (notification.type == NotificationType.REMINDER and booking.starts_at <= datetime.now(UTC))
        ):
            notification.status = NotificationStatus.SKIPPED
            db.commit()
            log.info("notification.skipped notification_id=%s booking_status=%s", notification_id,
                     booking.status if booking else None, extra={"notification_id": notification_id})  # fmt: skip
            return
        try:
            ticket_pdf = None
            if notification.type == NotificationType.CONFIRMATION:
                ticket = db.get(Ticket, notification.booking_id)
                if ticket is None:
                    raise TransientEmailError("the ticket is not rendered yet")
                ticket_pdf = storage.get_object_bytes(storage.s3_client(), ticket.bucket, ticket.object_key)
            message = emails.compose(settings, NotificationType(notification.type), booking, notification.recipient,
                                     notification.message_id, ticket_pdf)  # fmt: skip
            email_sender().send(message)
        except PermanentEmailError as exc:
            notification.status = NotificationStatus.FAILED
            notification.attempts += 1
            notification.last_error = str(exc)[:2000]
            db.commit()
            log.error("notification.failed notification_id=%s reason=permanent error=%s", notification_id, exc,
                      extra={"notification_id": notification_id, "booking_id": notification.booking_id})  # fmt: skip
            raise Reject(str(exc), requeue=False) from exc
        except (TransientEmailError, BotoCoreError, ClientError) as exc:
            db.rollback()
            retry_or_fail(self, notification_id, exc)
        notification.status = NotificationStatus.SENT
        notification.sent_at = datetime.now(UTC)
        notification.attempts += 1
        notification.last_error = None
        db.commit()
    log.info("notification.sent notification_id=%s type=%s", notification_id, notification.type,
             extra={"notification_id": notification_id, "booking_id": notification.booking_id})  # fmt: skip


# ---------------------------------------------------------------- Beat


def create_due_reminders(now: datetime) -> list[int]:
    """REMINDER notifications for confirmed departures lead_hours (± the window) from now. Safe to run twice."""
    centre = now + timedelta(hours=settings.reminder_lead_hours)
    window = timedelta(minutes=settings.reminder_window_minutes)
    with SessionLocal() as db:
        due = db.execute(
            select(BookingSnapshot).where(
                BookingSnapshot.status == "CONFIRMED",
                BookingSnapshot.contact_email.is_not(None),
                BookingSnapshot.starts_at >= centre - window,
                BookingSnapshot.starts_at < centre + window,
            )
        ).scalars()
        created = [
            notification_id
            for booking in due
            if (
                notification_id := create_notification(
                    db, booking.booking_id, booking.reference, booking.contact_email, NotificationType.REMINDER
                )
            )
            is not None
        ]
        db.commit()
    return created


@app.task(name="notifications.schedule_reminders")
def schedule_reminders() -> int:
    created = create_due_reminders(datetime.now(UTC))
    dispatch(SessionLocal, created)
    log.info("reminders.scheduled count=%s", len(created))
    return len(created)


@app.task(name="notifications.redispatch_pending")
def redispatch_pending() -> int:
    cutoff = datetime.now(UTC) - timedelta(seconds=settings.redispatch_after_s)
    with SessionLocal() as db:
        stuck = list(
            db.execute(
                select(Notification.id)
                .where(
                    Notification.status == NotificationStatus.PENDING,
                    Notification.created_at < cutoff,
                    or_(Notification.dispatched_at.is_(None), Notification.dispatched_at < cutoff),
                )
                .order_by(Notification.id)
                .limit(500)
            ).scalars()
        )
    if stuck:
        log.warning("notifications.redispatched count=%s", len(stuck))
        dispatch(SessionLocal, stuck)
    return len(stuck)


@app.task(name="notifications.relay_outbox")
def relay_outbox() -> int:
    published = 0
    try:
        while True:
            with SessionLocal() as db:
                batch = outbox.relay_batch(db, settings)
            published += batch
            if batch < settings.outbox_batch_size:
                return published
    except outbox.PublishNotConfirmedError as exc:
        # Nothing was marked; the next Beat run retries the same rows.
        log.warning("outbox.relay_failed published=%s error=%s", published, exc)
        return published
