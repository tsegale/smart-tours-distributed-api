"""Integration events this service consumes, and the handler that applies them."""

import logging
import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, ValidationError
from pydantic.alias_generators import to_camel
from sqlalchemy.orm import Session, sessionmaker

from . import repository
from .embeddings import Embedder, document_text

log = logging.getLogger(__name__)


class PoisonMessageError(Exception):
    """The message can never be processed (malformed, wrong schema). Retrying is pointless."""


class TourEvent(BaseModel):
    """Mirrors com.smarttours.core.events.TourCreatedEvent (camelCase JSON from Jackson)."""

    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True, frozen=True)

    event_id: uuid.UUID
    event_type: str
    occurred_at: datetime
    tour_id: int = Field(gt=0)
    name: str = Field(min_length=1)
    description: str | None = None


def parse_event(body: bytes) -> TourEvent:
    try:
        return TourEvent.model_validate_json(body)
    except ValidationError as exc:
        raise PoisonMessageError(f"Unparseable tour event: {exc.error_count()} validation error(s)") from exc


def handle_tour_event(body: bytes, session_factory: sessionmaker[Session], embedder: Embedder) -> TourEvent:
    event = parse_event(body)
    content = document_text(event.name, event.description)
    vector = embedder.embed(content)
    with session_factory() as db, db.begin():
        repository.upsert_embedding(
            db,
            tour_id=event.tour_id,
            title=event.name,
            content=content,
            embedding=vector,
            model=embedder.model_name,
            event_id=event.event_id,
        )
    log.info("Embedded tour %s (event %s)", event.tour_id, event.event_id)
    return event
