"""Contract tests: every event booking-service publishes matches its schema in docs/events (docs/events.md).

conftest.py validates every outbox row after every test. These tests make sure each event type is actually
produced at least once, and that the check would catch a payload that drifts from its schema.
"""

from sqlalchemy import select

from app.database import SessionLocal
from app.models import OutboxEvent
from app.services.outbox import AVAILABILITY_CHANGED, BOOKING_EVENTS, REVIEW_SUBMITTED
from tests.conftest import ALICE, EVENT_SCHEMAS
from tests.stripe_events import checkout_event, deliver
from tests.test_reviews import PAST, review
from tests.test_webhooks import COMPLETED, sweep_as_if_later

PRODUCED = {AVAILABILITY_CHANGED, REVIEW_SUBMITTED, *BOOKING_EVENTS.values()}


def published_payloads() -> list[dict]:
    with SessionLocal() as db:
        return list(db.execute(select(OutboxEvent.payload).order_by(OutboxEvent.occurred_at)).scalars())


def test_every_event_type_booking_service_produces_is_documented():
    assert set(EVENT_SCHEMAS) >= PRODUCED, f"undocumented: {sorted(PRODUCED - set(EVENT_SCHEMAS))}"


def test_one_of_each_event_type_matches_its_schema(client, departure, book):
    reviewed = book(departure(total_slots=10, starts_at=PAST), headers={**ALICE, "X-User-Email": "a@example.com"})
    deliver(client, checkout_event(COMPLETED, reviewed["id"]))
    assert review(client, reviewed["id"]).status_code == 201

    future = departure(total_slots=10, starts_at="2030-12-01T06:00:00+02:00")
    refunded = book(future)["id"]
    deliver(client, checkout_event(COMPLETED, refunded))
    client.post(f"/api/bookings/{refunded}/cancel", headers=ALICE)
    cancelled = book(future)["id"]
    client.post(f"/api/bookings/{cancelled}/cancel", headers=ALICE)
    book(future)
    sweep_as_if_later()

    payloads = published_payloads()
    assert {p["eventType"] for p in payloads} == PRODUCED
    for payload in payloads:
        errors = list(EVENT_SCHEMAS[payload["eventType"]].iter_errors(payload))
        assert not errors, (payload["eventType"], [e.message for e in errors])


def test_the_schemas_catch_drift():
    """The suite-wide check is only worth something if a wrong payload fails it."""
    good = {
        "eventId": "0b0b7a3e-4e4f-4f6a-9d63-2a4d3f0e9c11",
        "eventType": "review.submitted",
        "occurredAt": "2026-11-03T10:00:00+00:00",
        "reviewId": 1,
        "bookingId": 2,
        "tourId": 3,
        "rating": 4,
    }
    schema = EVENT_SCHEMAS["review.submitted"]
    assert not list(schema.iter_errors(good))
    assert list(schema.iter_errors({**good, "undocumented": True})), "unknown field"
    assert list(schema.iter_errors({**good, "rating": 6})), "out of range"
    assert list(schema.iter_errors({k: v for k, v in good.items() if k != "tourId"})), "missing field"
    assert list(schema.iter_errors({**good, "occurredAt": "yesterday"})), "not a date-time"

    refunded = EVENT_SCHEMAS["booking.refunded"].schema
    assert {"if": {"properties": {"eventType": {"const": "booking.refunded"}}}}.items() <= refunded["allOf"][2].items()
