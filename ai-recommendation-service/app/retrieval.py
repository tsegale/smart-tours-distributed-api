"""Hybrid retrieval in one SQL statement: dense and lexical candidates over the same filters, fused by rank (ADR 0019).

Filters apply inside the statement, to both candidate lists, not to the results in Python afterwards: a filter applied
after retrieval can leave a page nearly empty while plenty of matching tours sit further down the ranking.

Reciprocal Rank Fusion scores a tour by 1 / (k + rank) in each list it appears in. It needs no calibration between
cosine distances and text ranks, which aren't comparable, only their order.
"""

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from decimal import Decimal
from typing import Literal
from zoneinfo import ZoneInfo

from sqlalchemy import text
from sqlalchemy.orm import Session

WINDHOEK = ZoneInfo("Africa/Windhoek")
Mode = Literal["hybrid", "dense", "lexical"]
DEFAULT_RRF_K = 60
DEFAULT_CANDIDATES = 100


@dataclass(frozen=True)
class Filters:
    latitude: float | None = None
    longitude: float | None = None
    radius_km: float | None = None
    max_price: Decimal | None = None
    date_from: date | None = None
    date_to: date | None = None
    min_slots: int | None = None
    category: str | None = None
    difficulty: str | None = None
    language: str | None = None
    max_minutes: int | None = None
    min_minutes: int | None = None
    only_ids: tuple[int, ...] | None = None  # for evaluation over part of a corpus


@dataclass(frozen=True)
class Hit:
    tour_id: int
    dense_rank: int | None
    lexical_rank: int | None
    fused_score: float


def rrf_score(ranks: list[int | None], k: int = DEFAULT_RRF_K) -> float:
    """The formula the statement below applies. Kept here so it can be tested with plain numbers."""
    return sum(1.0 / (k + rank) for rank in ranks if rank is not None)


def _where(filters: Filters, params: dict) -> str:
    clauses = ["t.name <> ''"]  # a placeholder row: a review arrived before its tour
    if filters.latitude is not None and filters.longitude is not None and filters.radius_km is not None:
        clauses.append("ST_DWithin(t.location, ST_SetSRID(ST_MakePoint(:lon, :lat), 4326)::geography, :radius_m)")
        params |= {"lat": filters.latitude, "lon": filters.longitude, "radius_m": filters.radius_km * 1000}
    if filters.max_price is not None:
        clauses.append("t.price <= :max_price")
        params["max_price"] = filters.max_price
    if filters.category:
        clauses.append("t.category = :category")
        params["category"] = filters.category
    if filters.difficulty:
        clauses.append("t.difficulty = :difficulty")
        params["difficulty"] = filters.difficulty
    if filters.language:
        clauses.append("t.language = :language")
        params["language"] = filters.language
    if filters.max_minutes is not None:
        clauses.append("t.duration_minutes <= :max_minutes")
        params["max_minutes"] = filters.max_minutes
    if filters.min_minutes is not None:
        clauses.append("t.duration_minutes >= :min_minutes")
        params["min_minutes"] = filters.min_minutes
    if filters.only_ids is not None:
        clauses.append("t.tour_id = ANY(:only_ids)")
        params["only_ids"] = list(filters.only_ids)
    if filters.date_from or filters.date_to or filters.min_slots:
        departure = ["d.tour_id = t.tour_id", "d.slots_left >= :min_slots"]
        params["min_slots"] = filters.min_slots or 1
        # Whole days in Windhoek, whichever time zone the database session is in.
        if filters.date_from:
            departure.append("d.starts_at >= :from_ts")
            params["from_ts"] = datetime.combine(filters.date_from, time.min, WINDHOEK)
        if filters.date_to:
            departure.append("d.starts_at < :to_ts")
            params["to_ts"] = datetime.combine(filters.date_to + timedelta(days=1), time.min, WINDHOEK)
        clauses.append(f"EXISTS (SELECT 1 FROM ai_departures d WHERE {' AND '.join(departure)})")
    return " AND ".join(clauses)


NO_CANDIDATES = "SELECT NULL::bigint AS tour_id, NULL::bigint AS rank WHERE false"


def dense_candidates(where: str) -> str:
    """The nearest first and then their positions, in that order. A window function ordering by distance would sort
    every row before LIMIT got a look in, and keep the planner from using the HNSW index's order at all."""
    return f"""SELECT tour_id, row_number() OVER (ORDER BY distance) AS rank FROM (
                SELECT e.tour_id, e.embedding <=> CAST(:vector AS vector) AS distance
                FROM tour_embeddings e JOIN ai_tours t ON t.tour_id = e.tour_id
                WHERE e.model = :model AND {where}
                ORDER BY e.embedding <=> CAST(:vector AS vector) LIMIT :n
            ) nearest"""


def search(
    db: Session,
    *,
    query_text: str,
    query_vector: list[float] | None,
    model: str,
    filters: Filters = Filters(),
    mode: Mode = "hybrid",
    limit: int = 10,
    rrf_k: int = DEFAULT_RRF_K,
    candidates: int = DEFAULT_CANDIDATES,
) -> list[Hit]:
    params: dict = {"text": query_text, "model": model, "n": candidates, "limit": limit, "k": rrf_k}
    where = _where(filters, params)
    use_dense = mode in ("hybrid", "dense") and query_vector is not None
    use_lexical = mode in ("hybrid", "lexical") and query_text.strip() != ""
    if use_dense:
        params["vector"] = str(query_vector)
        # HNSW filters after it scans: keep scanning until enough rows pass the filter (pgvector 0.8).
        db.execute(text("SET LOCAL hnsw.iterative_scan = strict_order"))
        db.execute(text("SET LOCAL hnsw.ef_search = 100"))
    dense = dense_candidates(where) if use_dense else NO_CANDIDATES
    # Any term may match, and ranking decides. websearch_to_tsquery's implied AND returns nothing for a natural
    # sentence. Both languages' stemmers, since a query can be in either and a tour is stemmed in its own.
    lexical = (
        f"""SELECT tour_id, row_number() OVER (ORDER BY score DESC, tour_id) AS rank FROM (
                SELECT t.tour_id, ts_rank_cd(t.search_vector, q.q) AS score
                FROM ai_tours t,
                     (SELECT nullif(replace(plainto_tsquery('english', :text)::text, ' & ', ' | '), '')::tsquery
                          || nullif(replace(plainto_tsquery('german', :text)::text, ' & ', ' | '), '')::tsquery AS q) q
                WHERE t.search_vector @@ q.q AND {where}
                ORDER BY score DESC, t.tour_id LIMIT :n
            ) ranked"""
        if use_lexical
        else NO_CANDIDATES
    )
    statement = f"""
        WITH dense AS ({dense}), lexical AS ({lexical})
        SELECT COALESCE(dense.tour_id, lexical.tour_id) AS tour_id,
               dense.rank AS dense_rank,
               lexical.rank AS lexical_rank,
               COALESCE(1.0 / (:k + dense.rank), 0) + COALESCE(1.0 / (:k + lexical.rank), 0) AS fused_score
        FROM dense FULL JOIN lexical ON dense.tour_id = lexical.tour_id
        ORDER BY fused_score DESC, tour_id
        LIMIT :limit
    """
    rows = db.execute(text(statement), params).all()
    return [Hit(r.tour_id, r.dense_rank, r.lexical_rank, float(r.fused_score)) for r in rows]


def hydrate(db: Session, tour_ids: list[int]) -> dict[int, dict]:
    """What a result shows about a tour, by id."""
    if not tour_ids:
        return {}
    rows = db.execute(
        text(
            """
            SELECT tour_id, name, description, language, price, currency, category, difficulty, duration_minutes,
                   ST_Y(location::geometry) AS latitude, ST_X(location::geometry) AS longitude
            FROM ai_tours WHERE tour_id = ANY(:ids)
            """
        ),
        {"ids": tour_ids},
    ).mappings()
    return {row["tour_id"]: dict(row) for row in rows}
