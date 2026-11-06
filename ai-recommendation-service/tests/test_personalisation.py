"""Profile maths and MMR by hand, then the event handling and the endpoint on the real database."""

import json
import math
import os
import uuid
from datetime import UTC, datetime, timedelta

import pytest

from app.personalisation import Interaction, interaction_weight, mmr, profile_vector

NOW = datetime(2026, 11, 6, tzinfo=UTC)


def unit(*values: float) -> list[float]:
    norm = math.sqrt(sum(v * v for v in values))
    return [v / norm for v in values]


def test_a_year_of_decay_is_two_half_lives_at_180_days():
    old = Interaction(1, [1.0], NOW - timedelta(days=360))
    assert interaction_weight(old, NOW) == pytest.approx(0.25)


def test_a_high_rating_doubles_the_weight():
    assert interaction_weight(Interaction(1, [1.0], NOW, rating=5), NOW) == pytest.approx(2.0)
    assert interaction_weight(Interaction(1, [1.0], NOW, rating=3), NOW) == pytest.approx(1.0)
    assert interaction_weight(Interaction(1, [1.0], NOW), NOW) == pytest.approx(1.0)


def test_a_poor_rating_counts_for_nothing():
    assert interaction_weight(Interaction(1, [1.0], NOW, rating=2), NOW) == 0.0


def test_a_profile_is_a_unit_vector_leaning_towards_the_recent():
    recent = Interaction(1, [1.0, 0.0], NOW)
    old = Interaction(2, [0.0, 1.0], NOW - timedelta(days=720))
    profile = profile_vector([recent, old], NOW)
    assert math.sqrt(sum(v * v for v in profile)) == pytest.approx(1.0)
    assert profile[0] > profile[1] * 10


def test_no_usable_interaction_means_no_profile():
    assert profile_vector([], NOW) is None
    assert profile_vector([Interaction(1, [1.0, 0.0], NOW, rating=1)], NOW) is None


QUERY = [1.0, 0.0, 0.0]


def candidate(tour_id, *values):
    vector = unit(*values)
    return tour_id, vector, sum(a * b for a, b in zip(QUERY, vector, strict=True))


NEAR_TWIN_A = candidate(1, 0.95, 0.31, 0.0)
NEAR_TWIN_B = candidate(2, 0.94, 0.34, 0.0)
DIFFERENT = candidate(3, 0.80, 0.0, 0.60)


def test_lambda_one_is_plain_relevance():
    assert mmr([DIFFERENT, NEAR_TWIN_B, NEAR_TWIN_A], 3, lambda_=1.0) == [1, 2, 3]


def test_mmr_prefers_something_different_over_a_near_duplicate():
    assert mmr([NEAR_TWIN_A, NEAR_TWIN_B, DIFFERENT], 2, lambda_=0.5) == [1, 3]


def test_relevance_is_whatever_it_is_given_not_a_cosine():
    popular = (9, unit(0.0, 1.0, 0.0), 1.0)  # far from the query, but the fused score says it comes first
    assert mmr([NEAR_TWIN_A, popular], 1, lambda_=1.0) == [9]


def test_mmr_stops_at_k_and_at_the_candidates():
    assert len(mmr([NEAR_TWIN_A, NEAR_TWIN_B, DIFFERENT], 2)) == 2
    assert len(mmr([NEAR_TWIN_A], 5)) == 1
    assert mmr([], 5) == []


TEST_DATABASE_URL = os.environ.get("TEST_DATABASE_URL")
needs_database = pytest.mark.skipif(not TEST_DATABASE_URL, reason="TEST_DATABASE_URL not set")


@pytest.fixture
def database():
    from alembic import command
    from alembic.config import Config
    from sqlalchemy import text

    from app.database import SessionLocal

    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    cfg = Config(os.path.join(root, "alembic.ini"))
    cfg.attributes["configure_logger"] = False
    cfg.set_main_option("script_location", os.path.join(root, "migrations"))
    cfg.set_main_option("sqlalchemy.url", TEST_DATABASE_URL.replace("%", "%%"))
    command.upgrade(cfg, "head")
    with SessionLocal() as db, db.begin():
        db.execute(
            text(
                """
                TRUNCATE ai_tours, ai_departures, tour_embeddings, embedding_models, ai_bookings, ai_reviews,
                         ai_processed_events
                """
            )
        )
    return SessionLocal


def add_tour(sessions, tour_id, vector, *, slots=4, days_ahead=10, rating=(0, 0)):
    from app import read_model, repository

    with sessions() as db, db.begin():
        read_model.upsert_tour(
            db,
            tour_id=tour_id,
            name=f"Tour {tour_id}",
            description="",
            language="en",
            latitude=-22.5,
            longitude=17.0,
            price=100,
            currency="NAD",
            category="NATURE",
            difficulty="EASY",
            duration_minutes=60,
            version=1,
        )
        repository.upsert_embedding(
            db,
            tour_id=tour_id,
            title="t",
            content="t",
            embedding=vector,
            model="fake-embedder",
            event_id=None,
        )
        read_model.upsert_departure(
            db,
            availability_id=tour_id,
            tour_id=tour_id,
            starts_at=datetime.now(UTC) + timedelta(days=days_ahead),
            total_slots=8,
            slots_left=slots,
            version=1,
        )
        if rating[1]:
            from sqlalchemy import text

            db.execute(
                text("UPDATE ai_tours SET rating_sum = :s, rating_count = :c WHERE tour_id = :t"),
                {"s": rating[0], "c": rating[1], "t": tour_id},
            )


def vector(*head: float) -> list[float]:
    raw = list(head) + [0.0] * (384 - len(head))
    return unit(*raw)


def booking_event(booking_id, user, tour_id, event_type="booking.confirmed", when="2026-10-01T10:00:00+00:00"):
    return json.dumps(
        {
            "eventId": str(uuid.uuid4()),
            "eventType": event_type,
            "occurredAt": when,
            "schemaVersion": 1,
            "bookingId": booking_id,
            "reference": "ST-TEST",
            "userId": user,
            "contactEmail": "someone@example.com",
            "status": "CONFIRMED",
            "previousStatus": "PENDING_PAYMENT",
            "reason": "payment_succeeded",
            "slots": 2,
            "unitPrice": "100.00",
            "totalAmount": "200.00",
            "currency": "NAD",
            "departure": {"availabilityId": 7, "tourId": tour_id, "tourName": "Tour"},
        }
    ).encode()


def review_event(booking_id, tour_id, rating, event_id=None):
    return json.dumps(
        {
            "eventId": event_id or str(uuid.uuid4()),
            "eventType": "review.submitted",
            "occurredAt": "2026-10-20T10:00:00+00:00",
            "reviewId": booking_id,
            "bookingId": booking_id,
            "tourId": tour_id,
            "rating": rating,
        }
    ).encode()


def handle(sessions, body):
    from app.messages import handle_event
    from tests.fakes import FakeEmbedder

    handle_event(body, sessions, FakeEmbedder())


def recommend(sessions, user="alice", limit=3):
    from app.personalisation import recommend_for

    with sessions() as db:
        return recommend_for(db, user, "fake-embedder", limit, datetime.now(UTC))


@needs_database
def test_a_new_user_gets_what_is_popular(database):
    add_tour(database, 1, vector(1, 0))
    add_tour(database, 2, vector(0, 1))
    handle(database, booking_event(1, "bob", 2))
    assert recommend(database) == ("popular", [2, 1])


@needs_database
def test_popularity_breaks_ties_by_rating_with_enough_ratings_to_mean_something(database):
    add_tour(database, 1, vector(1, 0), rating=(5, 1))  # one perfect review
    add_tour(database, 2, vector(0, 1), rating=(225, 50))  # fifty reviews averaging 4.5
    assert recommend(database)[1] == [2, 1]


@needs_database
def test_recommendations_follow_taste_and_exclude_what_was_booked(database):
    add_tour(database, 1, vector(1, 0))
    add_tour(database, 2, vector(0.9, 0.1))
    add_tour(database, 3, vector(0, 1))
    handle(database, booking_event(1, "alice", 1))
    source, tours = recommend(database, limit=2)
    assert source == "personalised"
    assert tours[0] == 2
    assert 1 not in tours


@needs_database
def test_popularity_can_lift_a_tour_above_a_slightly_closer_one(database):
    add_tour(database, 1, vector(1, 0))
    add_tour(database, 2, vector(0.9, 0.1))
    add_tour(database, 3, vector(0.85, 0.15))
    for booking, user in enumerate(("bob", "carol", "dave"), start=10):
        handle(database, booking_event(booking, user, 3))
    handle(database, booking_event(1, "alice", 1))
    assert recommend(database, limit=1)[1] == [3]


@needs_database
def test_tied_popularity_has_no_say_so_a_low_tour_id_is_not_an_advantage(database):
    """With no bookings or ratings, every tour is equally popular. Ranking those by id pushed the closest match down."""
    add_tour(database, 1, vector(0.9, 0.1))
    add_tour(database, 2, vector(0.8, 0.2))
    add_tour(database, 3, vector(0.7, 0.3))
    add_tour(database, 4, vector(0.95, 0.05))  # the closest, and the newest
    add_tour(database, 9, vector(1, 0))
    handle(database, booking_event(1, "alice", 9))
    assert recommend(database, limit=1) == ("personalised", [4])


@needs_database
def test_only_bookable_tours_are_offered(database):
    add_tour(database, 1, vector(1, 0))
    add_tour(database, 2, vector(0.9, 0.1), slots=0)
    add_tour(database, 3, vector(0.8, 0.2), days_ahead=-1)
    add_tour(database, 4, vector(0, 1))
    handle(database, booking_event(1, "alice", 1))
    assert recommend(database)[1] == [4]


@needs_database
def test_a_cancelled_booking_stops_counting_and_can_be_offered_again(database):
    add_tour(database, 1, vector(1, 0))
    add_tour(database, 2, vector(0, 1))
    handle(database, booking_event(1, "alice", 1))
    handle(database, booking_event(1, "alice", 1, "booking.cancelled", "2026-10-02T10:00:00+00:00"))
    assert recommend(database)[0] == "popular"
    assert 1 in recommend(database)[1]


@needs_database
def test_an_older_status_event_arriving_late_does_not_undo_a_cancellation(database):
    add_tour(database, 1, vector(1, 0))
    handle(database, booking_event(1, "alice", 1, "booking.refunded", "2026-10-02T10:00:00+00:00"))
    handle(database, booking_event(1, "alice", 1))
    assert recommend(database)[0] == "popular"


@needs_database
def test_a_review_before_its_booking_still_counts(database):
    add_tour(database, 1, vector(1, 0))
    add_tour(database, 2, vector(0, 1))
    add_tour(database, 3, vector(0.9, 0.1))
    add_tour(database, 4, vector(0.1, 0.9))
    handle(database, review_event(5, 1, 1))  # hated it, before the booking event arrived
    handle(database, booking_event(5, "alice", 1))
    handle(database, booking_event(6, "alice", 2))
    assert recommend(database, limit=1)[1] == [4], "the disliked tour's direction carries no weight"


@needs_database
def test_a_duplicate_review_event_changes_nothing(database):
    from app.models import AiTour

    add_tour(database, 1, vector(1, 0))
    event_id = str(uuid.uuid4())
    handle(database, review_event(5, 1, 5, event_id))
    handle(database, review_event(5, 1, 5, event_id))
    with database() as db:
        assert db.get(AiTour, 1).rating_count == 1


@needs_database
def test_contact_details_are_not_stored(database):
    from sqlalchemy import text

    add_tour(database, 1, vector(1, 0))
    handle(database, booking_event(1, "alice", 1))
    with database() as db:
        row = db.execute(text("SELECT * FROM ai_bookings")).mappings().one()
    assert "someone@example.com" not in {str(value) for value in row.values()}
    assert set(row) == {"booking_id", "user_id", "tour_id", "confirmed", "occurred_at"}


@needs_database
def test_the_endpoint(database, monkeypatch):
    from fastapi.testclient import TestClient

    from app import main
    from tests.fakes import FakeEmbedder

    class Registry:
        def active(self):
            return FakeEmbedder()

    monkeypatch.setattr(main, "get_registry", Registry)
    add_tour(database, 1, vector(1, 0))
    add_tour(database, 2, vector(0.9, 0.1))
    handle(database, booking_event(1, "alice", 1))
    with TestClient(main.app) as client:
        assert client.get("/api/ai/recommendations/me").status_code == 401
        body = client.get("/api/ai/recommendations/me", headers={"X-User-Id": "alice"}).json()
    assert body["source"] == "personalised"
    assert [tour["tourId"] for tour in body["results"]] == [2]
    assert body["results"][0]["name"] == "Tour 2"
