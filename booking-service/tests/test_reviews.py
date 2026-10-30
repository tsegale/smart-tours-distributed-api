"""Verified reviews: owner only, confirmed only, after departure, once per booking, event via the outbox."""

import threading

from sqlalchemy import select

from app.database import SessionLocal
from app.models import OutboxEvent, Review
from tests.conftest import ALICE, BOB
from tests.stripe_events import checkout_event, deliver
from tests.test_webhooks import COMPLETED

PAST = "2026-01-10T06:00:00+02:00"
FUTURE = "2031-01-10T06:00:00+02:00"


def confirmed_booking(client, departure, book, starts_at: str = PAST, tour_id: int = 4) -> int:
    booking_id = book(departure(total_slots=10, tour_id=tour_id, starts_at=starts_at), slots=2)["id"]
    assert deliver(client, checkout_event(COMPLETED, booking_id)).status_code == 200
    return booking_id


def review(client, booking_id: int, headers=ALICE, **body):
    return client.post(f"/api/bookings/{booking_id}/review", json={"rating": 4, **body}, headers=headers)


def review_events() -> list[dict]:
    with SessionLocal() as db:
        return list(
            db.execute(select(OutboxEvent.payload).where(OutboxEvent.event_type == "review.submitted")).scalars()
        )


def test_owner_reviews_a_trip_that_has_started(client, departure, book):
    booking_id = confirmed_booking(client, departure, book)

    response = review(client, booking_id, rating=5, comment="Unforgettable sunrise.")

    assert response.status_code == 201, response.text
    body = response.json()
    assert body["rating"] == 5
    assert body["tour_id"] == 4
    assert body["comment"] == "Unforgettable sunrise."
    [event] = review_events()
    assert event == {
        "eventId": event["eventId"],
        "eventType": "review.submitted",
        "occurredAt": event["occurredAt"],
        "reviewId": body["id"],
        "bookingId": booking_id,
        "tourId": 4,
        "rating": 5,
    }


def test_someone_elses_booking_is_404(client, departure, book):
    booking_id = confirmed_booking(client, departure, book)

    response = review(client, booking_id, headers=BOB)

    assert response.status_code == 404
    assert review_events() == []


def test_unpaid_hold_cannot_be_reviewed(client, departure, book):
    booking_id = book(departure(starts_at=PAST))["id"]

    response = review(client, booking_id)

    assert response.status_code == 409
    assert response.json()["type"].endswith("/review-not-allowed")


def test_trip_that_has_not_started_cannot_be_reviewed(client, departure, book):
    booking_id = confirmed_booking(client, departure, book, starts_at=FUTURE)

    response = review(client, booking_id)

    assert response.status_code == 409
    assert response.json()["type"].endswith("/review-not-allowed")
    assert "starts_at" in response.json()


def test_cancelled_booking_cannot_be_reviewed(client, departure, book):
    booking_id = book(departure(starts_at=PAST))["id"]
    client.post(f"/api/bookings/{booking_id}/cancel", headers=ALICE)

    assert review(client, booking_id).status_code == 409


def test_second_review_is_409_and_writes_no_second_event(client, departure, book):
    booking_id = confirmed_booking(client, departure, book)
    assert review(client, booking_id).status_code == 201

    response = review(client, booking_id, rating=1)

    assert response.status_code == 409
    assert response.json()["type"].endswith("/review-already-submitted")
    assert len(review_events()) == 1


def test_concurrent_reviews_of_one_booking_store_exactly_one(client, departure, book):
    booking_id = confirmed_booking(client, departure, book)
    barrier = threading.Barrier(8)
    statuses: list[int] = []

    def submit() -> None:
        barrier.wait()
        statuses.append(review(client, booking_id).status_code)

    threads = [threading.Thread(target=submit) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(30)

    assert sorted(statuses) == [201] + [409] * 7
    with SessionLocal() as db:
        assert len(db.execute(select(Review)).scalars().all()) == 1
    assert len(review_events()) == 1


def test_rating_and_comment_are_validated(client, departure, book):
    booking_id = confirmed_booking(client, departure, book)

    assert review(client, booking_id, rating=0).status_code == 422
    assert review(client, booking_id, rating=6).status_code == 422
    assert review(client, booking_id, comment="x" * 2001).status_code == 422
    assert review_events() == []
