"""Recording events and scheduling reminders, against the real database: the idempotency layers of ADR 0014."""

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import func, select

from app.bridge import Action, decide
from app.database import SessionLocal
from app.events import PoisonMessageError, parse_booking_event
from app.intake import record_booking_event
from app.models import BookingSnapshot, Notification, NotificationType
from app.tasks import create_due_reminders
from tests.factories import body, booking_event


def record(event: dict) -> list[int]:
    with SessionLocal() as db:
        created = record_booking_event(db, parse_booking_event(body(event)))
        db.commit()
    return created


def notifications() -> list[tuple[int, str]]:
    with SessionLocal() as db:
        return list(db.execute(select(Notification.booking_id, Notification.type).order_by(Notification.id)).all())


def test_a_duplicate_event_creates_one_notification():
    event = booking_event("booking.confirmed")

    first = record(event)
    second = record(event)

    assert len(first) == 1
    assert second == []
    assert notifications() == [(18, NotificationType.CONFIRMATION)]


def test_the_same_change_published_twice_with_new_event_ids_still_notifies_once():
    """A relay crash between publish and mark republishes the row, with its eventId; a bug could mint a new one.
    The (booking_id, type) constraint is the layer that catches the second case."""
    occurred = datetime.now(UTC)
    record(booking_event("booking.confirmed", occurred_at=occurred))
    record(booking_event("booking.confirmed", occurred_at=occurred + timedelta(seconds=1)))

    assert notifications() == [(18, NotificationType.CONFIRMATION)]


def test_an_overtaken_event_sends_nothing():
    """A confirmation that arrives after the cancellation it preceded must not send a confirmation email."""
    now = datetime.now(UTC)
    record(booking_event("booking.cancelled", occurred_at=now))
    late = record(booking_event("booking.confirmed", occurred_at=now - timedelta(seconds=5)))

    assert late == []
    assert notifications() == [(18, NotificationType.CANCELLATION)]
    with SessionLocal() as db:
        assert db.get(BookingSnapshot, 18).status == "CANCELLED"


def test_without_a_contact_email_the_booking_is_recorded_but_nothing_is_owed():
    assert record(booking_event("booking.confirmed", email=None)) == []
    assert notifications() == []
    with SessionLocal() as db:
        assert db.get(BookingSnapshot, 18).status == "CONFIRMED"


@pytest.mark.parametrize(
    "raw",
    [
        b"not json",
        b'{"eventType": "booking.confirmed"}',
        body({**booking_event(), "eventType": "tour.created"}),
        body({**booking_event(), "schemaVersion": 2}),
    ],
)
def test_poison_messages_are_dead_lettered_without_retry(raw):
    with pytest.raises(PoisonMessageError):
        parse_booking_event(raw)
    assert decide(raw, None, lambda b: parse_booking_event(b), max_retries=5) is Action.DEAD_LETTER


def test_a_failing_handler_is_retried_then_dead_lettered():
    def broken(_: bytes) -> None:
        raise ConnectionError("database down")

    assert decide(b"{}", {"x-retry-count": 2}, broken, max_retries=5) is Action.RETRY
    assert decide(b"{}", {"x-retry-count": 5}, broken, max_retries=5) is Action.DEAD_LETTER


# ---------------------------------------------------------------- reminders


def confirmed_departing(booking_id: int, starts_at: datetime, time_zone: str = "Africa/Windhoek") -> None:
    record(booking_event("booking.confirmed", booking_id, starts_at=starts_at, time_zone=time_zone))


def reminders() -> list[int]:
    with SessionLocal() as db:
        return sorted(
            db.execute(select(Notification.booking_id).where(Notification.type == NotificationType.REMINDER)).scalars()
        )


def test_reminders_go_to_departures_a_day_ahead_within_the_window():
    now = datetime(2030, 12, 1, 8, 0, tzinfo=UTC)
    confirmed_departing(1, now + timedelta(hours=24))  # dead centre
    confirmed_departing(2, now + timedelta(hours=24, minutes=19))  # inside the 20-minute half-window
    confirmed_departing(3, now + timedelta(hours=24, minutes=21))  # next run's
    confirmed_departing(4, now + timedelta(hours=23, minutes=39))  # previous run's
    confirmed_departing(5, now + timedelta(hours=3))  # too soon: that reminder was due yesterday

    created = create_due_reminders(now)

    assert len(created) == 2
    assert reminders() == [1, 2]


def test_two_beat_runs_create_one_reminder():
    now = datetime(2030, 12, 1, 8, 0, tzinfo=UTC)
    confirmed_departing(1, now + timedelta(hours=24))

    first = create_due_reminders(now)
    second = create_due_reminders(now + timedelta(minutes=15))  # the next run overlaps the same departure

    assert len(first) == 1
    assert second == []
    assert reminders() == [1]
    with SessionLocal() as db:
        assert db.execute(select(func.count()).select_from(Notification)).scalar_one() == 2  # confirmation + reminder


def test_cancelled_bookings_get_no_reminder():
    now = datetime(2030, 12, 1, 8, 0, tzinfo=UTC)
    confirmed_departing(1, now + timedelta(hours=24))
    record(booking_event("booking.cancelled", 1, starts_at=now + timedelta(hours=24)))

    assert create_due_reminders(now) == []


@pytest.mark.parametrize(
    ("local_departure", "utc_departure"),
    [
        # Windhoek is UTC+2: five past midnight on the 2nd is still the 1st in UTC, five to midnight is the 1st in both.
        ("2030-12-02T00:05:00+02:00", datetime(2030, 12, 1, 22, 5, tzinfo=UTC)),
        ("2030-12-01T23:55:00+02:00", datetime(2030, 12, 1, 21, 55, tzinfo=UTC)),
    ],
)
def test_the_reminder_window_is_absolute_time_whatever_the_local_date(local_departure, utc_departure):
    departure = datetime.fromisoformat(local_departure)
    assert departure == utc_departure
    confirmed_departing(1, departure)

    assert create_due_reminders(utc_departure - timedelta(hours=24)) != []
    assert create_due_reminders(utc_departure - timedelta(hours=25)) == []
