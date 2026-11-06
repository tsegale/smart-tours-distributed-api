"""Personalised recommendations: a time-decayed taste vector, fused with popularity, then diversified (ADR 0022).

A new user has no vector, so gets what is popular. Either way only tours with a seat on a future departure are
offered, and never one the user has already booked.
"""

import math
from dataclasses import dataclass
from datetime import datetime

from pgvector.sqlalchemy import Vector
from sqlalchemy import text
from sqlalchemy.orm import Session

from .config import get_settings

HALF_LIFE_DAYS = 180.0
BOOKED_WEIGHT = 1.0
HIGHLY_RATED_BONUS = 1.0  # a booking rated 4 or 5 says more than a booking alone
HIGHLY_RATED = 4
POORLY_RATED = 2  # a tour the user disliked shouldn't pull them towards more like it
MMR_LAMBDA = 0.7
NEAR = 500
CANDIDATES = 50


@dataclass(frozen=True)
class Interaction:
    tour_id: int
    vector: list[float]
    occurred_at: datetime
    rating: int | None = None


def dot(a: list[float], b: list[float]) -> float:
    return sum(x * y for x, y in zip(a, b, strict=True))


def interaction_weight(interaction: Interaction, now: datetime, half_life_days: float = HALF_LIFE_DAYS) -> float:
    if interaction.rating is not None and interaction.rating <= POORLY_RATED:
        return 0.0
    weight = BOOKED_WEIGHT
    if interaction.rating is not None and interaction.rating >= HIGHLY_RATED:
        weight += HIGHLY_RATED_BONUS
    age_days = max((now - interaction.occurred_at).total_seconds() / 86400, 0.0)
    return weight * 0.5 ** (age_days / half_life_days)


def profile_vector(
    interactions: list[Interaction], now: datetime, half_life_days: float = HALF_LIFE_DAYS
) -> list[float] | None:
    """The weighted mean of what the user booked, recent and well rated counting most, as a unit vector, or None."""
    total: list[float] | None = None
    for interaction in interactions:
        weight = interaction_weight(interaction, now, half_life_days)
        if weight <= 0:
            continue
        scaled = [weight * value for value in interaction.vector]
        total = scaled if total is None else [a + b for a, b in zip(total, scaled, strict=True)]
    if total is None:
        return None
    norm = math.sqrt(sum(value * value for value in total))
    return [value / norm for value in total] if norm > 0 else None


def mmr(candidates: list[tuple[int, list[float], float]], k: int, lambda_: float = MMR_LAMBDA) -> list[int]:
    """Maximal Marginal Relevance over (tour id, unit vector, relevance from 0 to 1).

    Each pick trades its relevance against its closeness to what is already picked. lambda_ = 1 is relevance order.
    """
    vectors = {tour_id: vector for tour_id, vector, _ in candidates}
    relevance = {tour_id: score for tour_id, _, score in candidates}
    remaining = set(vectors)
    chosen: list[int] = []
    while remaining and len(chosen) < k:
        best = max(remaining, key=lambda tour_id: (_marginal(tour_id, vectors, relevance, chosen, lambda_), -tour_id))
        chosen.append(best)
        remaining.remove(best)
    return chosen


def _marginal(
    tour_id: int, vectors: dict[int, list[float]], relevance: dict[int, float], chosen: list[int], lambda_: float
) -> float:
    redundancy = max((dot(vectors[tour_id], vectors[picked]) for picked in chosen), default=0.0)
    return lambda_ * relevance[tour_id] - (1 - lambda_) * redundancy


# Bookable: a seat on a departure that hasn't left yet.
_AVAILABLE = """EXISTS (SELECT 1 FROM ai_departures d
                        WHERE d.tour_id = t.tour_id AND d.slots_left > 0 AND d.starts_at > now())"""


# Most booked first, then best rated with enough ratings to mean something (a mean pulled towards 3.5), then stable.
_BOOKINGS = """LEFT JOIN (SELECT tour_id, count(*) AS bookings FROM ai_bookings WHERE confirmed GROUP BY tour_id) b
                      ON b.tour_id = t.tour_id"""
_POPULARITY = """coalesce(b.bookings, 0) DESC, (t.rating_sum + 3.5 * 5) / (t.rating_count + 5) DESC, t.tour_id"""


def load_interactions(db: Session, user_id: str, model: str) -> tuple[list[Interaction], set[int]]:
    rows = db.execute(
        text(
            """
            SELECT b.tour_id, b.occurred_at, r.rating, e.embedding
            FROM ai_bookings b
            LEFT JOIN ai_reviews r ON r.booking_id = b.booking_id
            LEFT JOIN tour_embeddings e ON e.tour_id = b.tour_id AND e.model = :model
            WHERE b.user_id = :user AND b.confirmed
            """
        ).columns(embedding=Vector(get_settings().embedding_dimension)),
        {"user": user_id, "model": model},
    ).all()
    booked = {row.tour_id for row in rows}
    interactions = [
        Interaction(row.tour_id, [float(v) for v in row.embedding], row.occurred_at, row.rating)
        for row in rows
        if row.embedding is not None
    ]
    return interactions, booked


def fused_candidates(
    db: Session, profile: list[float], booked: set[int], model: str, limit: int = CANDIDATES
) -> list[tuple[int, list[float], float]]:
    """(tour id, vector, relevance): the tours nearest the profile, re-ordered by RRF with their popularity rank.

    Similarity alone ignores a signal the evaluation shows matters, and fusing ranks needs no calibration between a
    cosine and a booking count. Relevance is the fused score over the best one's, so MMR sees a value from 0 to 1.
    """
    rows = db.execute(
        text(
            f"""
            WITH near AS (
                SELECT tour_id, row_number() OVER (ORDER BY distance) AS rank, embedding FROM (
                    SELECT e.tour_id, e.embedding, e.embedding <=> CAST(:profile AS vector) AS distance
                    FROM tour_embeddings e JOIN ai_tours t ON t.tour_id = e.tour_id
                    WHERE e.model = :model AND t.name <> '' AND NOT (e.tour_id = ANY(:booked)) AND {_AVAILABLE}
                    ORDER BY e.embedding <=> CAST(:profile AS vector)
                    LIMIT :near
                ) nearest
            ), popular AS (
                SELECT t.tour_id, row_number() OVER (ORDER BY {_POPULARITY}) AS rank
                FROM ai_tours t {_BOOKINGS}
                WHERE t.name <> '' AND {_AVAILABLE}
            )
            SELECT near.tour_id, near.embedding,
                   1.0 / (60 + near.rank) + 1.0 / (60 + popular.rank) AS score
            FROM near JOIN popular ON popular.tour_id = near.tour_id
            ORDER BY score DESC, near.tour_id
            LIMIT :limit
            """
        ).columns(embedding=Vector(get_settings().embedding_dimension)),
        {"model": model, "booked": list(booked), "profile": str(profile), "near": NEAR, "limit": limit},
    ).all()
    if not rows:
        return []
    best = float(rows[0].score)
    return [(row.tour_id, [float(v) for v in row.embedding], float(row.score) / best) for row in rows]


def popular_available(db: Session, booked: set[int], limit: int) -> list[int]:
    statement = text(
        f"""
        SELECT t.tour_id
        FROM ai_tours t {_BOOKINGS}
        WHERE t.name <> '' AND NOT (t.tour_id = ANY(:booked)) AND {_AVAILABLE}
        ORDER BY {_POPULARITY}
        LIMIT :limit
        """
    )
    return list(db.execute(statement, {"booked": list(booked), "limit": limit}).scalars())


def recommend_for(db: Session, user_id: str, model: str, limit: int, now: datetime) -> tuple[str, list[int]]:
    """("personalised" or "popular", tour ids). The first says which logic answered."""
    interactions, booked = load_interactions(db, user_id, model)
    profile = profile_vector(interactions, now)
    if profile is not None:
        candidates = fused_candidates(db, profile, booked, model)
        if candidates:
            return "personalised", mmr(candidates, limit)
    return "popular", popular_available(db, booked, limit)
