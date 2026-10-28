import uuid

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from .models import TourEmbedding


def upsert_embedding(
    db: Session,
    *,
    tour_id: int,
    title: str,
    content: str,
    embedding: list[float],
    model: str,
    event_id: uuid.UUID | None,
) -> None:
    """Insert or replace a tour's vector. Idempotent, so a redelivered event is harmless."""
    stmt = insert(TourEmbedding).values(
        tour_id=tour_id, title=title, content=content, embedding=embedding, model=model, last_event_id=event_id
    )
    stmt = stmt.on_conflict_do_update(
        index_elements=[TourEmbedding.tour_id],
        set_={
            "title": stmt.excluded.title,
            "content": stmt.excluded.content,
            "embedding": stmt.excluded.embedding,
            "model": stmt.excluded.model,
            "last_event_id": stmt.excluded.last_event_id,
        },
    )
    db.execute(stmt)


def exists(db: Session, tour_id: int) -> bool:
    return db.get(TourEmbedding, tour_id) is not None


def similar_to_tour(db: Session, tour_id: int, limit: int) -> list[tuple[int, float]]:
    """Nearest neighbours of a stored tour by cosine distance (served by the HNSW index)."""
    target = select(TourEmbedding.embedding).where(TourEmbedding.tour_id == tour_id).scalar_subquery()
    distance = TourEmbedding.embedding.cosine_distance(target).label("distance")
    stmt = (
        select(TourEmbedding.tour_id, distance).where(TourEmbedding.tour_id != tour_id).order_by(distance).limit(limit)
    )
    return [(row.tour_id, float(row.distance)) for row in db.execute(stmt)]


def similar_to_vector(db: Session, vector: list[float], limit: int) -> list[tuple[int, float]]:
    distance = TourEmbedding.embedding.cosine_distance(vector).label("distance")
    stmt = select(TourEmbedding.tour_id, distance).order_by(distance).limit(limit)
    return [(row.tour_id, float(row.distance)) for row in db.execute(stmt)]
