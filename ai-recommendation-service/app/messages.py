"""Integration events this service consumes, and the handler that applies them."""

import json
import logging
import uuid
from collections.abc import Sequence
from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field, ValidationError
from pydantic.alias_generators import to_camel
from sqlalchemy.orm import Session, sessionmaker

from . import read_model, repository
from .embeddings import Embedder, document_text

log = logging.getLogger(__name__)


BOOKING_EVENTS = ("booking.confirmed", "booking.cancelled", "booking.refunded")


class PoisonMessageError(Exception):
    """The message can never be processed (malformed, wrong schema). Retrying is pointless."""


class _Event(BaseModel):
    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True, frozen=True)

    event_id: uuid.UUID
    event_type: str
    occurred_at: datetime


class TourEvent(_Event):
    """Mirrors com.smarttours.core.events.TourCreatedEvent (camelCase JSON from Jackson).

    The fields from `version` on arrived in Phase 6: an older event in a queue still applies, without them.
    """

    tour_id: int = Field(gt=0)
    name: str = Field(min_length=1)
    description: str | None = None
    version: int = 0
    latitude: float | None = None
    longitude: float | None = None
    price: Decimal | None = None
    currency: str | None = None
    category: str | None = None
    difficulty: str | None = None
    duration_minutes: int | None = None
    language: str = "en"


class AvailabilityChangedEvent(_Event):
    availability_id: int = Field(gt=0)
    tour_id: int = Field(gt=0)
    starts_at: datetime
    total_slots: int = Field(ge=0)
    slots_left: int = Field(ge=0)
    version: int = Field(ge=0)


class ReviewSubmittedEvent(_Event):
    tour_id: int = Field(gt=0)
    rating: int = Field(ge=1, le=5)
    # An event from before personalisation may lack it: it still counts towards the tour's rating.
    booking_id: int | None = None


class BookingDeparture(BaseModel):
    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True, frozen=True)

    tour_id: int = Field(gt=0)


class BookingLifecycleEvent(_Event):
    """Only what ranking needs. The event also carries contact details, which this model leaves behind on purpose."""

    booking_id: int = Field(gt=0)
    user_id: str = Field(min_length=1)
    departure: BookingDeparture


def _parse(model: type[_Event], body: bytes) -> _Event:
    try:
        return model.model_validate_json(body)
    except ValidationError as exc:
        raise PoisonMessageError(f"Unparseable {model.__name__}: {exc.error_count()} validation error(s)") from exc


def parse_event(body: bytes) -> TourEvent:
    return _parse(TourEvent, body)


def event_type(body: bytes) -> str:
    try:
        return str(json.loads(body)["eventType"])
    except (ValueError, KeyError, TypeError) as exc:
        raise PoisonMessageError("A message without a readable eventType") from exc


def handle_event(
    body: bytes, session_factory: sessionmaker[Session], embedder: Embedder, also: Sequence[Embedder] = ()
) -> object:
    """Route one message by its eventType. All three are idempotent and safe out of order.

    also: models being backfilled, which get a vector too, so that a tour edited mid-migration isn't left behind.
    """
    kind = event_type(body)
    if kind in ("tour.created", "tour.updated"):
        return handle_tour_event(body, session_factory, embedder, also)
    if kind == "availability.changed":
        event = _parse(AvailabilityChangedEvent, body)
        with session_factory() as db, db.begin():
            read_model.upsert_departure(
                db,
                availability_id=event.availability_id,
                tour_id=event.tour_id,
                starts_at=event.starts_at,
                total_slots=event.total_slots,
                slots_left=event.slots_left,
                version=event.version,
            )
        return event
    if kind == "review.submitted":
        event = _parse(ReviewSubmittedEvent, body)
        with session_factory() as db, db.begin():
            read_model.add_rating(db, event_id=event.event_id, tour_id=event.tour_id, rating=event.rating)
            if event.booking_id is not None:
                read_model.upsert_review(
                    db,
                    booking_id=event.booking_id,
                    tour_id=event.tour_id,
                    rating=event.rating,
                    occurred_at=event.occurred_at,
                )
        return event
    if kind in BOOKING_EVENTS:
        event = _parse(BookingLifecycleEvent, body)
        with session_factory() as db, db.begin():
            read_model.upsert_booking(
                db,
                booking_id=event.booking_id,
                user_id=event.user_id,
                tour_id=event.departure.tour_id,
                confirmed=kind == "booking.confirmed",
                occurred_at=event.occurred_at,
            )
        return event
    raise PoisonMessageError(f"An event this consumer doesn't handle: {kind}")


def handle_tour_event(
    body: bytes, session_factory: sessionmaker[Session], embedder: Embedder, also: Sequence[Embedder] = ()
) -> TourEvent:
    event = parse_event(body)
    content = document_text(event.name, event.description)
    # Outside the transaction: embedding is the slow part.
    vectors = [(e.model_name, e.embed_document(content)) for e in (embedder, *also)]
    with session_factory() as db, db.begin():
        applied = read_model.upsert_tour(
            db,
            tour_id=event.tour_id,
            name=event.name,
            description=event.description,
            language=event.language,
            latitude=event.latitude,
            longitude=event.longitude,
            price=event.price,
            currency=event.currency,
            category=event.category,
            difficulty=event.difficulty,
            duration_minutes=event.duration_minutes,
            version=event.version,
        )
        if not applied:
            log.info("Ignored stale tour %s v%s (event %s)", event.tour_id, event.version, event.event_id)
            return event
        for model, vector in vectors:
            repository.upsert_embedding(
                db,
                tour_id=event.tour_id,
                title=event.name,
                content=content,
                embedding=vector,
                model=model,
                event_id=event.event_id,
            )
    log.info("Embedded tour %s v%s (event %s)", event.tour_id, event.version, event.event_id)
    return event
