import json
import logging
import re
from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager
from datetime import date, datetime
from decimal import Decimal
from functools import lru_cache
from typing import Annotated, Literal
from zoneinfo import ZoneInfo

from fastapi import Depends, FastAPI, Header, Query, Request, status
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, ConfigDict, Field
from pydantic.alias_generators import to_camel
from sqlalchemy import text
from sqlalchemy.orm import Session

from . import repository
from .config import get_settings
from .database import SessionLocal, engine, get_db
from .embeddings import Embedder, document_text
from .errors import InvalidParameterError, NotFoundError, UnauthenticatedError, problem, register_error_handlers
from .gazetteer import load_csv
from .itinerary.service import Event, ItineraryQuery, PlannerConfig, plan_itinerary
from .itinerary.validator import Constraints
from .llm import LLMClient, get_llm_client
from .model_registry import ensure_active, get_registry
from .personalisation import recommend_for
from .query_parser import QueryParser
from .quota import TokenQuota, get_quota
from .reranker import OnnxCrossEncoder, get_reranker
from .retrieval import Filters, Hit, hydrate, search
from .telemetry import configure_logging, configure_telemetry, instrument_fastapi

configure_logging("ai-recommendation-service")
configure_telemetry("ai-recommendation-service")

log = logging.getLogger(__name__)

WINDHOEK = ZoneInfo("Africa/Windhoek")
MAX_TRIP_DAYS = 14


def get_embedder() -> Embedder:
    """The model reads use right now. A cutover reaches this within REFRESH_S, with no restart."""
    return get_registry().active()


@lru_cache
def get_parser() -> QueryParser:
    return QueryParser(load_csv())


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    with SessionLocal() as db, db.begin():
        ensure_active(db, get_settings().embedding_model)
    # Load everything once at startup, not on the first request.
    embedder = get_embedder()
    if hasattr(embedder, "warm_up"):
        embedder.warm_up()
    get_parser()
    reranker = get_reranker()
    if reranker:
        reranker.warm_up()
    yield
    engine.dispose()


app = FastAPI(
    title="Smart Tours - AI Recommendation Service",
    version="2.0.0",
    description="Hybrid tour search over pgvector and full-text search, and itineraries grounded in the catalogue.",
    lifespan=lifespan,
)
instrument_fastapi(app)
register_error_handlers(app)

DbSession = Annotated[Session, Depends(get_db)]
EmbedderDep = Annotated[Embedder, Depends(get_embedder)]
ParserDep = Annotated[QueryParser, Depends(get_parser)]
RerankerDep = Annotated[OnnxCrossEncoder | None, Depends(get_reranker)]
LlmDep = Annotated[LLMClient | None, Depends(get_llm_client)]


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


class _Camel(BaseModel):
    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)


class Explanation(_Camel):
    dense_rank: int | None = Field(description="Position by meaning. Null: not among the candidates by meaning.")
    lexical_rank: int | None = Field(description="Position by words. Null: not among the candidates by words.")
    fused_score: float = Field(description="Reciprocal Rank Fusion: the sum of 1 / (k + rank) over both lists")
    rerank_score: float | None = Field(default=None, description="Cross-encoder score, when re-ranking is on")


class SearchResult(_Camel):
    tour_id: int
    name: str
    description: str | None
    language: str
    price: Decimal | None
    currency: str | None
    category: str | None
    difficulty: str | None
    duration_minutes: int | None
    latitude: float | None
    longitude: float | None
    explain: Explanation | None = None


class SearchFilters(_Camel):
    latitude: float | None = None
    longitude: float | None = None
    radius_km: float | None = None
    max_price: Decimal | None = None
    date_from: date | None = None
    date_to: date | None = None
    min_slots: int | None = None
    language: str | None = None
    max_minutes: int | None = None
    min_minutes: int | None = None


class SearchResponse(_Camel):
    query: str
    ranking_text: str = Field(description="What was left to rank by, once phrases became filters")
    understood: dict[str, str] = Field(description="The phrases taken as filters, so that behaviour can be explained")
    filters: SearchFilters = Field(description="The filters that applied: explicit parameters beat parsed phrases")
    results: list[SearchResult]


def _near(value: str | None) -> tuple[float, float] | None:
    if value is None:
        return None
    match = re.fullmatch(r"\s*(-?\d+(?:\.\d+)?)\s*,\s*(-?\d+(?:\.\d+)?)\s*", value)
    if not match or abs(float(match[1])) > 90 or abs(float(match[2])) > 180:
        raise InvalidParameterError("near must be latitude,longitude, for example -22.56,17.08")
    return float(match[1]), float(match[2])


def _scored(rows: list[tuple[int, float]]) -> list[ScoredTour]:
    return [ScoredTour(tour_id=tour_id, similarity=round(1.0 - distance, 4)) for tour_id, distance in rows]


@app.get("/health", tags=["ops"])
def health() -> dict[str, str]:
    with engine.connect() as conn:
        conn.execute(text("SELECT 1"))
    return {"status": "UP"}


@app.post("/api/ai/embed", status_code=status.HTTP_201_CREATED, tags=["admin"])
def embed_tour(body: EmbedRequest, db: DbSession, embedder: EmbedderDep) -> dict[str, int | str]:
    """Manual (re)embedding of one tour. In normal operation, tour events drive embedding."""
    content = document_text(body.title, body.description)
    vector = embedder.embed_document(content)
    with db.begin():
        repository.upsert_embedding(
            db,
            tour_id=body.tour_id,
            title=body.title,
            content=content,
            embedding=vector,
            model=embedder.model_name,
            event_id=None,
        )
    return {"status": "embedded", "tour_id": body.tour_id}


@app.get("/api/ai/recommend/{tour_id}", response_model=RecommendationResponse, tags=["recommendations"])
def recommend(
    tour_id: int, db: DbSession, embedder: EmbedderDep, limit: Annotated[int, Query(ge=1, le=20)] = 3
) -> RecommendationResponse:
    if not repository.exists(db, tour_id, embedder.model_name):
        raise NotFoundError(f"Tour {tour_id} has no embedding yet")
    return RecommendationResponse(
        tour_id=tour_id, recommendations=_scored(repository.similar_to_tour(db, tour_id, limit, embedder.model_name))
    )


@app.get(
    "/api/ai/search",
    response_model=SearchResponse,
    response_model_by_alias=True,
    response_model_exclude_none=False,
    tags=["search"],
)
def hybrid_search(
    db: DbSession,
    embedder: EmbedderDep,
    parser: ParserDep,
    reranker: RerankerDep,
    q: Annotated[
        str, Query(min_length=2, max_length=300, description="e.g. 'easy kayaking near Walvis Bay under N$1500'")
    ],
    near: Annotated[str | None, Query(description="latitude,longitude")] = None,
    radius: Annotated[float | None, Query(gt=0, le=2000, description="Kilometres around near")] = None,
    max_price: Annotated[Decimal | None, Query(alias="maxPrice", gt=0)] = None,
    date_from: Annotated[date | None, Query(alias="from")] = None,
    date_to: Annotated[date | None, Query(alias="to")] = None,
    min_slots: Annotated[int | None, Query(alias="minSlots", ge=1)] = None,
    lang: Annotated[Literal["en", "de"] | None, Query(description="Only tours written in this language")] = None,
    mode: Literal["hybrid", "dense", "lexical"] = "hybrid",
    explain: bool = False,
    limit: Annotated[int, Query(ge=1, le=50)] = 10,
) -> SearchResponse:
    parsed = parser.parse(q)
    point = _near(near)
    settings = get_settings()
    filters = Filters(
        latitude=point[0] if point else parsed.latitude,
        longitude=point[1] if point else parsed.longitude,
        radius_km=radius or parsed.radius_km or (settings.default_near_radius_km if point else None),
        max_price=max_price if max_price is not None else parsed.max_price,
        date_from=date_from or parsed.date_from,
        date_to=date_to or parsed.date_to,
        min_slots=min_slots,
        language=lang,
        max_minutes=parsed.max_minutes,
        min_minutes=parsed.min_minutes,
    )
    vector = embedder.embed_query(parsed.text) if mode != "lexical" else None  # outside the transaction: the slow part
    depth = max(limit, settings.rerank_depth) if reranker else limit
    with db.begin():
        hits = search(
            db,
            query_text=parsed.text,
            query_vector=vector,
            model=embedder.model_name,
            filters=filters,
            mode=mode,
            limit=depth,
            rrf_k=settings.rrf_k,
        )
        tours = hydrate(db, [hit.tour_id for hit in hits])

    rerank_scores: dict[int, float] = {}
    if reranker and hits:
        documents = [document_text(tours[h.tour_id]["name"], tours[h.tour_id]["description"]) for h in hits]
        rerank_scores = dict(zip((h.tour_id for h in hits), reranker.score(parsed.text, documents), strict=True))
        hits = sorted(hits, key=lambda h: (-rerank_scores[h.tour_id], h.tour_id))

    return SearchResponse(
        query=q,
        ranking_text=parsed.text,
        understood=parsed.understood,
        filters=SearchFilters(
            latitude=filters.latitude,
            longitude=filters.longitude,
            radius_km=filters.radius_km,
            max_price=filters.max_price,
            date_from=filters.date_from,
            date_to=filters.date_to,
            min_slots=filters.min_slots,
            language=filters.language,
            max_minutes=filters.max_minutes,
            min_minutes=filters.min_minutes,
        ),
        results=[_result(hit, tours[hit.tour_id], rerank_scores, explain) for hit in hits[:limit]],
    )


def _result(hit: Hit, tour: dict, rerank_scores: dict[int, float], explain: bool) -> SearchResult:
    explanation = None
    if explain:
        explanation = Explanation(
            dense_rank=hit.dense_rank,
            lexical_rank=hit.lexical_rank,
            fused_score=round(hit.fused_score, 6),
            rerank_score=rerank_scores.get(hit.tour_id),
        )
    return SearchResult(**tour, explain=explanation)


class PersonalResult(_Camel):
    tour_id: int
    name: str
    description: str | None
    price: Decimal | None
    currency: str | None
    category: str | None
    duration_minutes: int | None


class PersonalRecommendations(_Camel):
    source: Literal["personalised", "popular"] = Field(description="popular: no booking history to go on yet")
    results: list[PersonalResult]


class ItineraryRequest(_Camel):
    prompt: str = Field(min_length=3, max_length=1000)
    start_date: date
    days: int = Field(ge=1, le=MAX_TRIP_DAYS)
    party_size: int = Field(ge=1, le=50)
    budget: Decimal = Field(gt=0, max_digits=12, decimal_places=2, description="For the whole party, in NAD")
    near: str | None = Field(default=None, description="latitude,longitude")
    radius_km: float | None = Field(default=None, gt=0, le=2000)


def _user_id(x_user_id: Annotated[str | None, Header()] = None) -> str:
    """Identity comes only from the gateway's header."""
    if not x_user_id:
        raise UnauthenticatedError("This is for signed-in users")
    return x_user_id


def _quota_dependency() -> TokenQuota | None:
    return get_quota() if get_llm_client() is not None else None


@app.get(
    "/api/ai/recommendations/me",
    response_model=PersonalRecommendations,
    response_model_by_alias=True,
    tags=["recommendations"],
)
def my_recommendations(
    db: DbSession,
    embedder: EmbedderDep,
    user_id: Annotated[str, Depends(_user_id)],
    limit: Annotated[int, Query(ge=1, le=20)] = 10,
) -> PersonalRecommendations:
    """Bookable tours like the ones this user booked and liked, diversified. For a new user, what is popular."""
    source, tour_ids = recommend_for(db, user_id, embedder.model_name, limit, datetime.now(WINDHOEK))
    tours = hydrate(db, tour_ids)
    return PersonalRecommendations(source=source, results=[PersonalResult(**tours[t]) for t in tour_ids])


def _sse(kind: str, data: dict) -> str:
    return f"event: {kind}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


def _stream(events: Iterator[Event], instance: str) -> Iterator[str]:
    # The status line went out with the first byte, so a late failure can only be reported as an event.
    try:
        for event in events:
            yield _sse(event.kind, event.data)
    except Exception:
        log.exception("Itinerary planning failed mid-stream")
        yield _sse("error", problem(500, "itinerary-failed", "Planning failed", "Try again in a moment.", instance))


@app.post(
    "/api/ai/itineraries",
    tags=["itineraries"],
    responses={
        200: {
            "description": "The plan as JSON, or with Accept: text/event-stream a stream of `progress` events "
            "and one `plan` event",
            "content": {"text/event-stream": {}},
        }
    },
)
def create_itinerary(
    body: ItineraryRequest,
    request: Request,
    user_id: Annotated[str, Depends(_user_id)],
    embedder: EmbedderDep,
    parser: ParserDep,
    llm: LlmDep,
    quota: Annotated[TokenQuota | None, Depends(_quota_dependency)],
):
    """A day-by-day plan built only from real tours with seats on those dates. `path` says what produced it."""
    today = datetime.now(WINDHOEK).date()
    if body.start_date < today:
        raise InvalidParameterError(f"startDate {body.start_date} is in the past: today in Windhoek is {today}")
    point = _near(body.near)
    settings = get_settings()
    query = ItineraryQuery(
        prompt=body.prompt,
        constraints=Constraints(body.start_date, body.days, body.party_size, body.budget),
        latitude=point[0] if point else None,
        longitude=point[1] if point else None,
        radius_km=body.radius_km or (settings.default_near_radius_km if point else None),
    )
    config = PlannerConfig(settings.llm_max_tokens, settings.llm_attempt_timeout_s, settings.llm_deadline_s)
    events = plan_itinerary(
        query,
        user_id=user_id,
        session_factory=SessionLocal,
        embedder=embedder,
        parser=parser,
        llm=llm,
        quota=quota,
        config=config,
    )
    if "text/event-stream" in request.headers.get("accept", ""):
        return StreamingResponse(
            _stream(events, request.url.path),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )
    final = next(event for event in events if event.kind == "plan")
    return JSONResponse(final.data)
