import uuid
from datetime import datetime
from decimal import Decimal

from geoalchemy2 import Geography
from pgvector.sqlalchemy import Vector
from sqlalchemy import BigInteger, Computed, DateTime, Integer, Numeric, String, Text, Uuid, func
from sqlalchemy.dialects.postgresql import TSVECTOR
from sqlalchemy.orm import Mapped, mapped_column

from .config import get_settings
from .database import Base


class TourEmbedding(Base):
    """Read model owned by the AI service: one vector per tour, kept in sync by events.

    tour_id mirrors tour-core-service's id. There is no cross-service foreign key: this
    service's data is eventually consistent with the tour catalogue.
    """

    __tablename__ = "tour_embeddings"

    tour_id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=False)
    title: Mapped[str] = mapped_column(Text)
    content: Mapped[str] = mapped_column(Text)
    embedding: Mapped[list[float]] = mapped_column(Vector(get_settings().embedding_dimension))
    model: Mapped[str] = mapped_column(String(200))
    last_event_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class AiTour(Base):
    """What a hybrid query filters on, kept in step with tour-core by events (ADR 0019)."""

    __tablename__ = "ai_tours"

    tour_id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=False)
    name: Mapped[str] = mapped_column(Text)
    description: Mapped[str | None] = mapped_column(Text)
    language: Mapped[str] = mapped_column(String(2), default="en")
    location = mapped_column(Geography("POINT", srid=4326, spatial_index=False))
    price: Mapped[Decimal | None] = mapped_column(Numeric(10, 2))
    currency: Mapped[str | None] = mapped_column(String(3))
    category: Mapped[str | None] = mapped_column(String(30))
    difficulty: Mapped[str | None] = mapped_column(String(30))
    duration_minutes: Mapped[int | None] = mapped_column(Integer)
    rating_count: Mapped[int] = mapped_column(Integer, default=0)
    rating_sum: Mapped[int] = mapped_column(BigInteger, default=0)
    version: Mapped[int] = mapped_column(BigInteger, default=0)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    search_vector = mapped_column(TSVECTOR, Computed("search_vector", persisted=True))


class AiDeparture(Base):
    __tablename__ = "ai_departures"

    availability_id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=False)
    tour_id: Mapped[int] = mapped_column(BigInteger)
    starts_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    total_slots: Mapped[int] = mapped_column(Integer)
    slots_left: Mapped[int] = mapped_column(Integer)
    version: Mapped[int] = mapped_column(BigInteger)


class AiProcessedEvent(Base):
    __tablename__ = "ai_processed_events"

    event_id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True)
    processed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
