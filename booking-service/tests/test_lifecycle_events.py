"""Booking lifecycle events: one per status change, in the transaction that makes it, carrying what a notifier needs.

The autouse fixture in conftest.py already checks, after every test in the suite, that each booking's newest
lifecycle event matches its status. These tests pin down the contents of each event type and that nothing is
written for a change that is refused or rolled back.
"""

import pytest
from sqlalchemy import func, select

from app.database import SessionLocal
from app.domain.booking_status import transition
from app.models import Booking, BookingStatus, OutboxEvent
from app.services import outbox
from tests.conftest import ALICE, as_user
from tests.stripe_events import checkout_event, deliver
from tests.test_webhooks import ASYNC_FAILED, COMPLETED, EXPIRED, sweep_as_if_later

ALICE_WITH_EMAIL = {**ALICE, "X-User-Email": "alice@example.com"}


def lifecycle_events(booking_id: int) -> list[dict]:
    with SessionLocal() as db:
        return list(
            db.execute(
                select(OutboxEvent.payload)
                .where(OutboxEvent.aggregate_type == "booking", OutboxEvent.aggregate_id == str(booking_id))
                .order_by(OutboxEvent.occurred_at)
            ).scalars()
        )


def lifecycle_count() -> int:
    with SessionLocal() as db:
        return db.execute(
            select(func.count()).select_from(OutboxEvent).where(OutboxEvent.aggregate_type == "booking")
        ).scalar_one()


def test_confirmation_carries_the_booking_the_contact_and_the_departure_snapshot(client, departure, book):
    availability_id = departure(total_slots=10, starts_at="2030-12-01T06:00:00+02:00")
    booking = book(availability_id, slots=2, headers=ALICE_WITH_EMAIL)

    deliver(client, checkout_event(COMPLETED, booking["id"]))

    [event] = lifecycle_events(booking["id"])
    assert event["eventType"] == "booking.confirmed"
    assert event["eventId"] and event["occurredAt"]
    assert event["schemaVersion"] == 1
    assert event["bookingId"] == booking["id"]
    assert event["reference"] == booking["reference"]
    assert event["reference"].startswith("ST-") and len(event["reference"]) == 11
    assert event["userId"] == "alice"
    assert event["contactEmail"] == "alice@example.com"
    assert (event["previousStatus"], event["status"], event["reason"]) == (
        "PENDING_PAYMENT",
        "CONFIRMED",
        "payment_succeeded",
    )
    assert (event["slots"], event["unitPrice"], event["totalAmount"], event["currency"]) == (
        2,
        "1850.00",
        "3700.00",
        "NAD",
    )
    assert event["departure"] == {
        "availabilityId": availability_id,
        "tourId": 1,
        "tourName": "Sossusvlei Sunrise Dunes",
        "meetingPoint": "Sesriem gate",
        "timeZone": "Africa/Windhoek",
        "startsAt": "2030-12-01T04:00:00+00:00",
    }
    assert "refund" not in event


def test_a_booking_without_an_email_has_a_null_contact(client, departure, book):
    booking = book(departure())

    deliver(client, checkout_event(COMPLETED, booking["id"]))

    [event] = lifecycle_events(booking["id"])
    assert event["contactEmail"] is None


def test_an_early_cancellation_is_refunded_in_full(client, departure, book):
    booking = book(departure(starts_at="2030-12-01T06:00:00+02:00"), slots=3, headers=ALICE_WITH_EMAIL)
    deliver(client, checkout_event(COMPLETED, booking["id"]))

    response = client.post(f"/api/bookings/{booking['id']}/cancel", headers=ALICE)

    assert response.status_code == 200, response.text
    confirmed, refunded = lifecycle_events(booking["id"])
    assert confirmed["eventType"] == "booking.confirmed"
    assert refunded["eventType"] == "booking.refunded"
    assert (refunded["previousStatus"], refunded["status"]) == ("CONFIRMED", "REFUNDED")
    assert refunded["reason"] == "customer_cancelled_full_refund"
    assert refunded["refund"] == {"amount": "5550.00", "currency": "NAD"}


def test_cancelling_a_hold_and_a_failed_async_payment_are_cancellations(client, departure, book):
    availability_id = departure(total_slots=10)
    cancelled_hold = book(availability_id)["id"]
    failed_payment = book(availability_id)["id"]

    client.post(f"/api/bookings/{cancelled_hold}/cancel", headers=ALICE)
    deliver(client, checkout_event(ASYNC_FAILED, failed_payment))

    [hold] = lifecycle_events(cancelled_hold)
    [failed] = lifecycle_events(failed_payment)
    assert (hold["eventType"], hold["reason"]) == ("booking.cancelled", "customer_cancelled_hold")
    assert (failed["eventType"], failed["reason"]) == ("booking.cancelled", "async_payment_failed")


def test_a_lapsed_hold_and_an_expired_checkout_are_hold_expired(client, departure, book):
    availability_id = departure(total_slots=10)
    lapsed = book(availability_id)["id"]
    expired_checkout = book(availability_id)["id"]

    deliver(client, checkout_event(EXPIRED, expired_checkout))
    sweep_as_if_later()

    [swept] = lifecycle_events(lapsed)
    [webhook] = lifecycle_events(expired_checkout)
    assert (swept["eventType"], swept["reason"]) == ("booking.hold_expired", "hold_expired")
    assert (webhook["eventType"], webhook["reason"]) == ("booking.hold_expired", "checkout_expired")
    assert swept["holdExpiresAt"]


@pytest.mark.parametrize(("seats_still_free", "expected"), [(True, "booking.confirmed"), (False, "booking.refunded")])
def test_a_late_payment_confirms_or_refunds(client, departure, book, seats_still_free, expected):
    availability_id = departure(total_slots=2)
    late = book(availability_id, slots=2)["id"]
    sweep_as_if_later()
    if not seats_still_free:
        book(availability_id, slots=2, headers=as_user("bob"))

    deliver(client, checkout_event(COMPLETED, late))

    expired, settled = lifecycle_events(late)
    assert expired["eventType"] == "booking.hold_expired"
    assert settled["eventType"] == expected
    assert settled["previousStatus"] == "EXPIRED"


def test_a_refused_or_rolled_back_change_writes_no_lifecycle_event(client, departure, book):
    booking_id = book(departure())["id"]
    before = lifecycle_count()

    with SessionLocal() as db:
        booking = db.get(Booking, booking_id)
        outbox.booking_status_changed(db, booking, transition(booking, BookingStatus.CANCELLED, reason="test"))
        db.rollback()
    client.post(f"/api/bookings/{booking_id}/cancel", headers=as_user("bob"))  # not bob's: 404, nothing changes

    assert lifecycle_count() == before
