import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Annotated

from fastapi import Depends, FastAPI, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.orm import Session

from . import repository
from .database import engine, get_db
from .embeddings import Embedder, document_text, get_embedder

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    embedder = get_embedder()
    if hasattr(embedder, "warm_up"):
        embedder.warm_up()  # load the model once at startup, not on the first request
    yield
    engine.dispose()


app = FastAPI(
    title="Smart Tours - AI Recommendation Service",
    version="1.0.0",
    description="Semantic tour recommendations and search over sentence embeddings stored in pgvector.",
    lifespan=lifespan,
)

DbSession = Annotated[Session, Depends(get_db)]
EmbedderDep = Annotated[Embedder, Depends(get_embedder)]


class EmbedRequest(BaseModel):
    tour_id: int = Field(gt=0)
    title: str = Field(min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=5000)


class ScoredTour(BaseModel):
    tour_id: int
    similarity: float = Field(description="Cosine similarity, 1.0 = identical meaning")


class RecommendationResponse(BaseModel):
    tour_id: int
    recommendations: list[ScoredTour]


class SearchResponse(BaseModel):
    query: str
    results: list[ScoredTour]


def _scored(rows: list[tuple[int, float]]) -> list[ScoredTour]:
    return [ScoredTour(tour_id=tour_id, similarity=round(1.0 - distance, 4)) for tour_id, distance in rows]


@app.get("/health", tags=["ops"])
def health() -> dict[str, str]:
    with engine.connect() as conn:
        conn.execute(text("SELECT 1"))
    return {"status": "UP"}


@app.post("/api/ai/embed", status_code=status.HTTP_201_CREATED, tags=["admin"])
def embed_tour(body: EmbedRequest, db: DbSession, embedder: EmbedderDep) -> dict[str, int | str]:
    """Manual (re)embedding, e.g. to backfill. In normal operation, tour events drive embedding."""
    content = document_text(body.title, body.description)
    with db.begin():
        repository.upsert_embedding(
            db,
            tour_id=body.tour_id,
            title=body.title,
            content=content,
            embedding=embedder.embed(content),
            model=embedder.model_name,
            event_id=None,
        )
    return {"status": "embedded", "tour_id": body.tour_id}


@app.get("/api/ai/recommend/{tour_id}", response_model=RecommendationResponse, tags=["recommendations"])
def recommend(tour_id: int, db: DbSession, limit: Annotated[int, Query(ge=1, le=20)] = 3) -> RecommendationResponse:
    if not repository.exists(db, tour_id):
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"Tour {tour_id} has no embedding yet")
    return RecommendationResponse(
        tour_id=tour_id, recommendations=_scored(repository.similar_to_tour(db, tour_id, limit))
    )


@app.get("/api/ai/search", response_model=SearchResponse, tags=["recommendations"])
def semantic_search(
    db: DbSession,
    embedder: EmbedderDep,
    q: Annotated[str, Query(min_length=3, max_length=300, description="e.g. 'sunset photography, no long hikes'")],
    limit: Annotated[int, Query(ge=1, le=20)] = 5,
) -> SearchResponse:
    return SearchResponse(query=q, results=_scored(repository.similar_to_vector(db, embedder.embed(q), limit)))
