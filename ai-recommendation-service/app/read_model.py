"""Keeps the AI service's read model in step with the catalogue. Every apply is safe to repeat and to reorder.

Tours and departures carry a version, and an event applies only if it is newer, the rule tour-core's summary uses. A
review has no version, because each one adds to a total: its eventId is recorded in the same transaction instead.
"""

import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import func, text
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from .models import AiDeparture, AiProcessedEvent, AiTour


def point(latitude: float | None, longitude: float | None):
    if latitude is None or longitude is None:
        return None
    return func.ST_SetSRID(func.ST_MakePoint(longitude, latitude), 4326)


def upsert_tour(
    db: Session,
    *,
    tour_id: int,
    name: str,
    description: str | None,
    language: str,
    latitude: float | None,
    longitude: float | None,
    price: Decimal | None,
    currency: str | None,
    category: str | None,
    difficulty: str | None,
    duration_minutes: int | None,
    version: int,
) -> bool:
    """Apply a tour's state if it is newer than what is held. True if it was applied."""
    values = {
        "name": name,
        "description": description,
        "language": language,
        "location": point(latitude, longitude),
        "price": price,
        "currency": currency,
        "category": category,
        "difficulty": difficulty,
        "duration_minutes": duration_minutes,
        "version": version,
    }
    statement = insert(AiTour).values(tour_id=tour_id, **values)
    applied = db.execute(
        statement.on_conflict_do_update(
            index_elements=[AiTour.tour_id],
            set_={**{key: statement.excluded[key] for key in values}, "updated_at": func.now()},
            # >=, not >: a re-embed of the same version is harmless, and version 0 events predate the field.
            where=AiTour.version <= statement.excluded.version,
        ).returning(AiTour.tour_id)
    ).first()
    return applied is not None


def upsert_departure(
    db: Session,
    *,
    availability_id: int,
    tour_id: int,
    starts_at: datetime,
    total_slots: int,
    slots_left: int,
    version: int,
) -> bool:
    values = {
        "tour_id": tour_id,
        "starts_at": starts_at,
        "total_slots": total_slots,
        "slots_left": slots_left,
        "version": version,
    }
    statement = insert(AiDeparture).values(availability_id=availability_id, **values)
    applied = db.execute(
        statement.on_conflict_do_update(
            index_elements=[AiDeparture.availability_id],
            set_={key: statement.excluded[key] for key in values},
            where=AiDeparture.version < statement.excluded.version,
        ).returning(AiDeparture.availability_id)
    ).first()
    return applied is not None


def add_rating(db: Session, *, event_id: uuid.UUID, tour_id: int, rating: int) -> bool:
    """Add one review to the tour's rating, once per event, even if its tour hasn't arrived yet."""
    first_time = db.execute(
        insert(AiProcessedEvent)
        .values(event_id=event_id)
        .on_conflict_do_nothing(index_elements=[AiProcessedEvent.event_id])
        .returning(AiProcessedEvent.event_id)
    ).first()
    if first_time is None:
        return False
    # A review can overtake its tour's first event: a placeholder at version 0, which any real event then replaces.
    statement = insert(AiTour).values(tour_id=tour_id, name="", rating_count=1, rating_sum=rating, version=0)
    db.execute(
        statement.on_conflict_do_update(
            index_elements=[AiTour.tour_id],
            set_={"rating_count": AiTour.rating_count + 1, "rating_sum": AiTour.rating_sum + rating},
        )
    )
    return True


def analyze(db: Session) -> None:
    """After a bulk load: the planner needs statistics to choose between the indexes."""
    db.execute(text("ANALYZE ai_tours"))
    db.execute(text("ANALYZE ai_departures"))
