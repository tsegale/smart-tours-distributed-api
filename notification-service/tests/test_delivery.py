"""End to end: an event through the bridge, Celery on the real broker, the PDF in MinIO, the email in Mailpit."""

import io
import json
import os
from pathlib import Path

import pika
from jsonschema import Draft202012Validator, FormatChecker
from pypdf import PdfReader
from sqlalchemy import select

from app.bridge import handle
from app.celery_app import DEAD_LETTER_QUEUE
from app.database import SessionLocal
from app.dispatch import dispatch
from app.events import parse_booking_event
from app.intake import record_booking_event
from app.models import Notification, NotificationStatus, OutboxEvent, Ticket
from app.tasks import relay_outbox
from app.tickets import key_id
from tests.conftest import SIGNING_KEY, eventually
from tests.factories import body, booking_event

SCHEMAS = Path(__file__).resolve().parents[2] / "docs" / "events"


def notification(booking_id: int = 18) -> Notification | None:
    with SessionLocal() as db:
        return db.execute(select(Notification).where(Notification.booking_id == booking_id)).scalars().first()


def status_is(expected: NotificationStatus, booking_id: int = 18):
    def check():
        current = notification(booking_id)
        return current if current is not None and current.status == expected else None

    return check


def dead_letters() -> int:
    connection = pika.BlockingConnection(pika.URLParameters(os.environ["TEST_CELERY_BROKER_URL"]))
    try:
        return connection.channel().queue_declare(DEAD_LETTER_QUEUE, passive=True).method.message_count
    finally:
        connection.close()


def test_a_duplicate_confirmation_sends_exactly_one_email_with_the_ticket(celery_worker, mailpit):
    event = booking_event("booking.confirmed")

    handle(body(event))
    handle(body(event))  # at-least-once delivery: the same event again

    sent = eventually(status_is(NotificationStatus.SENT), what="the confirmation to be sent")
    messages = eventually(mailpit.messages, what="the email in Mailpit")
    assert len(messages) == 1
    message = mailpit.message(messages[0]["ID"])
    assert f"<{message['MessageID']}>" == sent.message_id
    assert message["To"][0]["Address"] == "alice@example.com"
    assert message["Subject"] == "Your booking ST-00000018 is confirmed: Sossusvlei Sunrise Dunes & Deadvlei"
    assert message["Text"] and message["HTML"]
    [attachment] = message["Attachments"]
    assert attachment["FileName"] == "ticket-ST-00000018.pdf"
    pdf = mailpit.attachment(messages[0]["ID"], attachment["PartID"])
    assert "ST-00000018" in PdfReader(io.BytesIO(pdf)).pages[0].extract_text()


def test_the_rendered_ticket_is_recorded_and_announced_with_the_documented_schema(celery_worker, mailpit):
    handle(body(booking_event("booking.confirmed")))
    eventually(status_is(NotificationStatus.SENT), what="the confirmation")

    with SessionLocal() as db:
        ticket = db.get(Ticket, 18)
        [event] = db.execute(select(OutboxEvent.payload)).scalars().all()
    assert ticket.object_key == "tickets/18/ST-00000018.pdf"
    assert ticket.key_id == key_id(SIGNING_KEY.public_key())  # what the rotation runbook queries
    schema = json.loads((SCHEMAS / "ticket-rendered.schema.json").read_text(encoding="utf-8"))
    errors = list(Draft202012Validator(schema, format_checker=FormatChecker()).iter_errors(event))
    assert not errors, [e.message for e in errors]
    assert (event["bookingId"], event["objectKey"], event["sha256"]) == (18, ticket.object_key, ticket.sha256)

    assert relay_outbox() >= 0  # publishes, or leaves it for the next run if the broker is busy
    eventually(lambda: _published(), what="ticket.rendered to be published")


def _published() -> bool:
    with SessionLocal() as db:
        return db.execute(select(OutboxEvent.published_at)).scalar_one() is not None


def test_a_transient_smtp_failure_is_retried_then_sent(celery_worker, mailpit):
    mailpit.chaos(451)  # "try again later" on every recipient
    handle(body(booking_event("booking.cancelled")))

    retried = eventually(lambda: (n := notification()) is not None and n.attempts >= 1 and n, what="a failed attempt")
    assert retried.status == NotificationStatus.PENDING
    assert "451" in retried.last_error
    mailpit.chaos(None)

    sent = eventually(status_is(NotificationStatus.SENT), what="the retried email")
    assert sent.attempts >= 2
    assert len(mailpit.messages()) == 1


def test_a_permanent_smtp_failure_fails_at_once_into_the_dead_letter_queue(celery_worker, mailpit):
    before = dead_letters()
    mailpit.chaos(550)  # mailbox unavailable: retrying won't help
    handle(body(booking_event("booking.refunded")))

    failed = eventually(status_is(NotificationStatus.FAILED), what="the notification to fail")
    assert failed.attempts == 1
    assert "550" in failed.last_error
    eventually(lambda: dead_letters() == before + 1, what="the task in the dead-letter queue")
    assert mailpit.messages() == []


def test_retries_exhausted_fail_into_the_dead_letter_queue(celery_worker, mailpit):
    before = dead_letters()
    mailpit.chaos(421)  # transient, but it never clears
    handle(body(booking_event("booking.hold_expired")))

    failed = eventually(status_is(NotificationStatus.FAILED), timeout=90, what="retries to run out")
    assert failed.attempts == 4  # the first try and TASK_MAX_RETRIES=3 retries
    assert "retries exhausted" in failed.last_error
    eventually(lambda: dead_letters() == before + 1, what="the task in the dead-letter queue")


def test_a_confirmation_for_a_booking_cancelled_since_is_skipped(celery_worker, mailpit):
    # Both events are recorded before either task runs: the cancellation lands while the confirmation is queued.
    created = []
    with SessionLocal() as db:
        for event in (booking_event("booking.confirmed"), booking_event("booking.cancelled")):
            created += record_booking_event(db, parse_booking_event(body(event)))
        db.commit()
    dispatch(SessionLocal, created)

    eventually(lambda: notification() is not None and all(
        n.status != NotificationStatus.PENDING for n in _all()), what="both notifications to settle")  # fmt: skip
    statuses = {n.type: n.status for n in _all()}
    assert statuses["CANCELLATION"] == NotificationStatus.SENT
    assert statuses["CONFIRMATION"] == NotificationStatus.SKIPPED
    assert [m["Subject"] for m in mailpit.messages()] == ["Your booking ST-00000018 is cancelled"]


def _all() -> list[Notification]:
    with SessionLocal() as db:
        return list(db.execute(select(Notification)).scalars())
