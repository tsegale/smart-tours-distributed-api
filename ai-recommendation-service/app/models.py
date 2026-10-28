import uuid
from datetime import datetime

from pgvector.sqlalchemy import Vector
from sqlalchemy import BigInteger, DateTime, String, Text, Uuid, func
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
