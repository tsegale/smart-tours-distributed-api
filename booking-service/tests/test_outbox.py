"""Seat changes write availability.changed to the outbox in the same transaction.

The autouse fixture in conftest.py already checks, after every test in the suite, that each
departure's newest event matches the row. These tests pin down the event contents, the version
sequence, and that nothing is written for a change that rolls back.
"""

import pytest
from sqlalchemy import func, select

from app.database import SessionLocal
from app.main import app
from app.models import OutboxEvent, TourAvailability
from app.routers.bookings import get_reservation_strategy
from app.services import outbox
from app.services.reservation import AtomicReservation, OptimisticReservation, PessimisticReservation
from app.services.sweeper import expire_overdue_holds
from tests.conftest import ALICE
from tests.stripe_events import checkout_event, deliver
from tests.test_webhooks import COMPLETED, EXPIRED, sweep_as_if_later


def availability_events(availability_id: int) -> list[dict]:
    with SessionLocal() as db:
        return list(
            db.execute(
                select(OutboxEvent.payload)
                .where(
                    OutboxEvent.event_type == "availability.changed",
                    OutboxEvent.aggregate_id == str(availability_id),
                )
                .order_by(OutboxEvent.occurred_at)
            ).scalars()
        )


def outbox_count() -> int:
    with SessionLocal() as db:
        return db.execute(select(func.count()).select_from(OutboxEvent)).scalar_one()


STRATEGIES = {
    "pessimistic": PessimisticReservation(),
    "optimistic": OptimisticReservation(max_attempts=8, backoff_base_ms=1, backoff_cap_ms=5),
    "atomic": AtomicReservation(),
}


@pytest.fixture(params=list(STRATEGIES))
def strategy(request):
    app.dependency_overrides[get_reservation_strategy] = lambda: STRATEGIES[request.param]
    yield request.param
    app.dependency_overrides.pop(get_reservation_strategy, None)


def test_opening_a_departure_publishes_its_full_state(departure):
    availability_id = departure(total_slots=12, tour_id=7, starts_at="2030-12-01T06:00:00+02:00")

    [event] = availability_events(availability_id)

    assert event["eventType"] == "availability.changed"
    assert event["eventId"]
    assert event["availabilityId"] == availability_id
    assert event["tourId"] == 7
    assert event["startsAt"] == "2030-12-01T04:00:00+00:00"
    assert event["totalSlots"] == 12
    assert event["slotsLeft"] == 12
    assert isinstance(event["version"], int)


def test_every_strategy_writes_the_new_seat_count_and_version(strategy, departure, book):
    availability_id = departure(total_slots=10)

    book(availability_id, slots=3)
    book(availability_id, slots=2)

    events = availability_events(availability_id)
    assert [e["slotsLeft"] for e in events] == [10, 7, 5]
    versions = [e["version"] for e in events]
    assert versions == sorted(versions) and len(set(versions)) == 3, "versions must strictly increase"
    with SessionLocal() as db:
        assert db.get(TourAvailability, availability_id).version == versions[-1]


def test_a_rejected_booking_writes_no_event(client, departure):
    availability_id = departure(total_slots=1)
    before = outbox_count()

    response = client.post("/api/bookings", json={"availability_id": availability_id, "slots": 2}, headers=ALICE)

    assert response.status_code == 409
    assert outbox_count() == before


def test_rolled_back_change_leaves_no_outbox_row(departure):
    availability_id = departure(total_slots=5)
    before = outbox_count()

    with SessionLocal() as db:
        availability = AtomicReservation().reserve(db, availability_id, 2)
        outbox.availability_changed(db, availability)
        db.rollback()

    assert outbox_count() == before
    assert [e["slotsLeft"] for e in availability_events(availability_id)] == [5]


def test_releases_publish_the_seats_coming_back(client, departure, book):
    availability_id = departure(total_slots=10)
    cancelled = book(availability_id, slots=4)["id"]
    expired = book(availability_id, slots=3)["id"]
    book(availability_id, slots=2)  # left to lapse

    client.post(f"/api/bookings/{cancelled}/cancel", headers=ALICE)  # customer cancels the hold
    deliver(client, checkout_event(EXPIRED, expired))  # provider says the checkout expired
    expire_overdue_holds(SessionLocal, batch_size=100)  # nothing overdue yet: no event
    sweep_as_if_later()  # the last hold lapses

    assert [e["slotsLeft"] for e in availability_events(availability_id)] == [10, 6, 3, 1, 5, 8, 10]


def test_late_payment_rereserve_publishes(client, departure, book):
    availability_id = departure(total_slots=5)
    booking_id = book(availability_id, slots=2)["id"]
    sweep_as_if_later()

    deliver(client, checkout_event(COMPLETED, booking_id))

    assert [e["slotsLeft"] for e in availability_events(availability_id)] == [5, 3, 5, 3]


def test_confirming_a_hold_changes_no_seats_and_writes_no_event(client, departure, book):
    availability_id = departure(total_slots=5)
    booking_id = book(availability_id, slots=2)["id"]

    deliver(client, checkout_event(COMPLETED, booking_id))

    assert [e["slotsLeft"] for e in availability_events(availability_id)] == [5, 3]
