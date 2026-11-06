"""The read model against real Postgres with pgvector and PostGIS: repeats and reordering change nothing."""

import json
import os
import uuid
from datetime import UTC, datetime
from decimal import Decimal

import pytest

TEST_DATABASE_URL = os.environ.get("TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not TEST_DATABASE_URL, reason="TEST_DATABASE_URL not set; needs pgvector and PostGIS")

from alembic import command  # noqa: E402
from alembic.config import Config  # noqa: E402
from sqlalchemy import text  # noqa: E402

from app import read_model  # noqa: E402
from app.database import SessionLocal, engine  # noqa: E402
from app.messages import PoisonMessageError, handle_event  # noqa: E402
from app.models import AiDeparture, AiTour, TourEmbedding  # noqa: E402
from tests.fakes import FakeEmbedder  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


@pytest.fixture(scope="module", autouse=True)
def schema():
    cfg = Config(os.path.join(ROOT, "alembic.ini"))
    cfg.attributes["configure_logger"] = False
    cfg.set_main_option("script_location", os.path.join(ROOT, "migrations"))
    cfg.set_main_option("sqlalchemy.url", TEST_DATABASE_URL.replace("%", "%%"))
    command.downgrade(cfg, "base")
    command.upgrade(cfg, "head")
    yield
    engine.dispose()


@pytest.fixture(autouse=True)
def clean():
    with SessionLocal() as db, db.begin():
        db.execute(text("TRUNCATE ai_tours, ai_departures, ai_processed_events, tour_embeddings"))


def tour_event(version: int, name: str = "Sossusvlei dunes", tour_id: int = 7, **extra) -> bytes:
    return json.dumps(
        {
            "eventId": str(uuid.uuid4()),
            "eventType": "tour.updated",
            "occurredAt": datetime.now(UTC).isoformat(),
            "tourId": tour_id,
            "name": name,
            "description": "Red sand at dawn",
            "version": version,
            "latitude": -24.73,
            "longitude": 15.29,
            "price": 1850.00,
            "currency": "NAD",
            "category": "NATURE",
            "difficulty": "MODERATE",
            "durationMinutes": 240,
            **extra,
        }
    ).encode()


def availability_event(version: int, slots_left: int, availability_id: int = 3) -> bytes:
    return json.dumps(
        {
            "eventId": str(uuid.uuid4()),
            "eventType": "availability.changed",
            "occurredAt": datetime.now(UTC).isoformat(),
            "availabilityId": availability_id,
            "tourId": 7,
            "startsAt": "2030-12-01T06:00:00+00:00",
            "totalSlots": 10,
            "slotsLeft": slots_left,
            "version": version,
        }
    ).encode()


def review_event(event_id: str, rating: int = 5) -> bytes:
    return json.dumps(
        {
            "eventId": event_id,
            "eventType": "review.submitted",
            "occurredAt": datetime.now(UTC).isoformat(),
            "reviewId": 1,
            "bookingId": 1,
            "tourId": 7,
            "rating": rating,
        }
    ).encode()


def apply(body: bytes) -> None:
    handle_event(body, SessionLocal, FakeEmbedder())


def tour(tour_id: int = 7) -> AiTour:
    with SessionLocal() as db:
        return db.get(AiTour, tour_id)


def test_a_tour_event_fills_the_read_model_and_its_embedding():
    apply(tour_event(1))

    stored = tour()
    assert (stored.name, stored.price, stored.category, stored.duration_minutes) == (
        "Sossusvlei dunes",
        Decimal("1850.00"),
        "NATURE",
        240,
    )
    with SessionLocal() as db:
        assert db.get(TourEmbedding, (7, FakeEmbedder.model_name)) is not None
        latitude, longitude = db.execute(
            text("SELECT ST_Y(location::geometry), ST_X(location::geometry) FROM ai_tours WHERE tour_id = 7")
        ).one()
        lexeme = db.execute(text("SELECT search_vector @@ to_tsquery('english', 'dune') FROM ai_tours")).scalar_one()
    assert (latitude, longitude) == (pytest.approx(-24.73), pytest.approx(15.29))
    assert lexeme, "the search vector is stemmed: 'dunes' matches 'dune'"


def test_an_overtaken_tour_event_changes_nothing():
    apply(tour_event(5, name="Newest name"))
    apply(tour_event(4, name="Stale name"))

    assert tour().name == "Newest name"
    with SessionLocal() as db:
        assert "Newest name" in db.get(TourEmbedding, (7, FakeEmbedder.model_name)).content, (
            "the embedding must not go stale either"
        )


def test_a_duplicate_tour_event_is_harmless():
    event = tour_event(2)
    apply(event)
    apply(event)

    with SessionLocal() as db:
        assert db.execute(text("SELECT count(*) FROM ai_tours")).scalar_one() == 1


def test_an_event_from_before_these_fields_still_applies():
    apply(
        json.dumps(
            {
                "eventId": str(uuid.uuid4()),
                "eventType": "tour.created",
                "occurredAt": datetime.now(UTC).isoformat(),
                "tourId": 8,
                "name": "Old format",
                "description": None,
            }
        ).encode()
    )

    stored = tour(8)
    assert stored.name == "Old format"
    assert stored.location is None


def test_german_tours_stem_in_german():
    apply(tour_event(1, name="Wanderungen im Gebirge", tour_id=9, language="de"))
    with SessionLocal() as db:
        german = db.execute(
            text("SELECT search_vector @@ to_tsquery('german', 'Wanderung') FROM ai_tours WHERE tour_id = 9")
        ).scalar_one()
    assert german


def test_an_overtaken_availability_event_changes_nothing():
    apply(availability_event(version=5, slots_left=2))
    apply(availability_event(version=4, slots_left=9))

    with SessionLocal() as db:
        assert db.get(AiDeparture, 3).slots_left == 2


def test_a_newer_availability_event_applies():
    apply(availability_event(version=1, slots_left=9))
    apply(availability_event(version=2, slots_left=8))

    with SessionLocal() as db:
        assert db.get(AiDeparture, 3).slots_left == 8


def test_a_repeated_review_counts_once():
    event_id = str(uuid.uuid4())
    apply(tour_event(1))
    apply(review_event(event_id, rating=4))
    apply(review_event(event_id, rating=4))
    apply(review_event(str(uuid.uuid4()), rating=2))

    stored = tour()
    assert (stored.rating_count, stored.rating_sum) == (2, 6)


def test_a_review_that_overtakes_its_tour_keeps_its_rating():
    apply(review_event(str(uuid.uuid4()), rating=5))
    apply(tour_event(1))

    stored = tour()
    assert stored.name == "Sossusvlei dunes"
    assert (stored.rating_count, stored.rating_sum) == (1, 5)


def test_an_unknown_event_or_a_malformed_one_is_poison():
    with pytest.raises(PoisonMessageError):
        apply(b'{"eventType": "something.else"}')
    with pytest.raises(PoisonMessageError):
        apply(b"not json")
    with pytest.raises(PoisonMessageError):
        apply(review_event(str(uuid.uuid4()), rating=9))


def test_read_model_analyze_runs():
    with SessionLocal() as db, db.begin():
        read_model.analyze(db)
