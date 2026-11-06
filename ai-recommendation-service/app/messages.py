"""Integration events this service consumes, and the handler that applies them."""

import json
import logging
import uuid
from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field, ValidationError
from pydantic.alias_generators import to_camel
from sqlalchemy.orm import Session, sessionmaker

from . import read_model, repository
from .embeddings import Embedder, document_text

log = logging.getLogger(__name__)


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


def handle_event(body: bytes, session_factory: sessionmaker[Session], embedder: Embedder) -> object:
    """Route one message by its eventType. All three are idempotent and safe out of order."""
    kind = event_type(body)
    if kind in ("tour.created", "tour.updated"):
        return handle_tour_event(body, session_factory, embedder)
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
        return event
    raise PoisonMessageError(f"An event this consumer doesn't handle: {kind}")


def handle_tour_event(body: bytes, session_factory: sessionmaker[Session], embedder: Embedder) -> TourEvent:
    event = parse_event(body)
    content = document_text(event.name, event.description)
    vector = embedder.embed_document(content)  # outside the transaction: embedding is the slow part
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
        repository.upsert_embedding(
            db,
            tour_id=event.tour_id,
            title=event.name,
            content=content,
            embedding=vector,
            model=embedder.model_name,
            event_id=event.event_id,
        )
    log.info("Embedded tour %s v%s (event %s)", event.tour_id, event.version, event.event_id)
    return event
