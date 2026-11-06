"""The pipeline: parse, retrieve, plan, validate, repair, fall back. A generator of events, so SSE can stream its
progress.

The database connection is released before the model is called: an LLM call takes seconds, and the pool is small.
"""

import logging
import time
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from datetime import time as day_time
from decimal import Decimal

from opentelemetry import metrics
from sqlalchemy import text
from sqlalchemy.orm import Session

from ..embeddings import Embedder
from ..llm import LLMClient, LlmError, complete_with_retries
from ..query_parser import ParsedQuery, QueryParser
from ..quota import QuotaUnavailableError, Reservation, TokenQuota
from ..retrieval import Filters, search
from .planner import deterministic_plan
from .prompt import PLAN_SCHEMA, SYSTEM, build_user_message, parse_plan, repair_message
from .validator import WINDHOEK, Catalogue, Constraints, Departure, ProposedItem, Violation, validate

log = logging.getLogger(__name__)

MAX_REPAIRS = 2
RETRIEVED_TOURS = 30
DEPARTURES_PER_TOUR = 5

_plans = metrics.get_meter("smarttours.ai").create_counter(
    "smarttours.itinerary.plans", description="Itineraries returned, by the path that produced them"
)


@dataclass(frozen=True)
class ItineraryQuery:
    prompt: str
    constraints: Constraints
    latitude: float | None = None
    longitude: float | None = None
    radius_km: float | None = None


@dataclass(frozen=True)
class PlannerConfig:
    max_tokens: int = 16000
    attempt_timeout_s: float = 60.0
    deadline_s: float = 150.0


@dataclass(frozen=True)
class Event:
    kind: str  # "progress" or "plan"
    data: dict


def load_catalogue(db: Session, tour_ids: list[int], constraints: Constraints) -> Catalogue:
    """Real departures on the trip's dates with seats for the party, and the distances between the tours they belong
    to."""
    if not tour_ids:
        return Catalogue()
    window_start = datetime.combine(constraints.start_date, day_time.min, WINDHOEK)
    window_end = datetime.combine(constraints.end_date + timedelta(days=1), day_time.min, WINDHOEK)
    rows = db.execute(
        text(
            """
            SELECT d.availability_id, d.tour_id, d.starts_at, d.slots_left,
                   t.name, coalesce(t.description, '') AS description, t.price, t.duration_minutes
            FROM ai_departures d JOIN ai_tours t ON t.tour_id = d.tour_id
            WHERE d.tour_id = ANY(:ids) AND d.starts_at >= :start AND d.starts_at < :end AND d.slots_left >= :party
              AND t.price IS NOT NULL AND t.currency = 'NAD' AND t.duration_minutes IS NOT NULL
              AND t.location IS NOT NULL
            ORDER BY d.starts_at, d.availability_id
            """
        ),
        {"ids": tour_ids, "start": window_start, "end": window_end, "party": constraints.party_size},
    ).all()
    rank = {tour_id: position for position, tour_id in enumerate(tour_ids)}
    kept: dict[int, Departure] = {}
    per_tour: dict[int, set[date]] = {}
    for row in rows:
        # One departure a day per tour, and only so many per tour: this all goes into a prompt.
        day = row.starts_at.astimezone(WINDHOEK).date()
        days_seen = per_tour.setdefault(row.tour_id, set())
        if day in days_seen or len(days_seen) >= DEPARTURES_PER_TOUR:
            continue
        days_seen.add(day)
        kept[row.availability_id] = Departure(
            availability_id=row.availability_id,
            tour_id=row.tour_id,
            name=row.name,
            description=row.description,
            starts_at=row.starts_at,
            duration_minutes=row.duration_minutes,
            price=row.price,
            slots_left=row.slots_left,
            rank=rank[row.tour_id],
        )
    catalogue = Catalogue(departures=kept)
    pairs = db.execute(
        text(
            """
            SELECT a.tour_id AS low, b.tour_id AS high, ST_Distance(a.location, b.location) / 1000 AS km
            FROM ai_tours a JOIN ai_tours b ON a.tour_id < b.tour_id
            WHERE a.tour_id = ANY(:ids) AND b.tour_id = ANY(:ids)
            """
        ),
        {"ids": sorted(catalogue.tour_ids)},
    ).all()
    catalogue.distances_km = {(p.low, p.high): float(p.km) for p in pairs}
    return catalogue


def _retrieve(
    session_factory: Callable[[], Session], embedder: Embedder, query: ItineraryQuery, parsed: ParsedQuery
) -> Catalogue:
    constraints = query.constraints
    latitude = query.latitude if query.latitude is not None else parsed.latitude
    longitude = query.longitude if query.longitude is not None else parsed.longitude
    radius = query.radius_km or parsed.radius_km
    filters = Filters(
        latitude=latitude,
        longitude=longitude,
        radius_km=radius,
        max_price=parsed.max_price,
        date_from=constraints.start_date,
        date_to=constraints.end_date,
        min_slots=constraints.party_size,
        max_minutes=parsed.max_minutes,
        min_minutes=parsed.min_minutes,
    )
    vector = embedder.embed_query(parsed.text)  # before the transaction: embedding is the slow part
    with session_factory() as db, db.begin():
        hits = search(
            db,
            query_text=parsed.text,
            query_vector=vector,
            model=embedder.model_name,
            filters=filters,
            limit=RETRIEVED_TOURS,
        )
        return load_catalogue(db, [hit.tour_id for hit in hits], constraints)


def _estimate_tokens(*texts: str) -> int:
    """A deliberately high estimate for a reservation: about three characters a token. The real count settles it."""
    return sum(len(t) for t in texts) // 3


@dataclass
class _LlmOutcome:
    items: list[ProposedItem] | None = None
    attempts: int = 0
    reason: str | None = None
    violations: list[str] | None = None
    input_tokens: int = 0
    output_tokens: int = 0


def _plan_with_llm(
    query: ItineraryQuery,
    catalogue: Catalogue,
    user_id: str,
    llm: LLMClient,
    quota: TokenQuota,
    config: PlannerConfig,
    outcome: _LlmOutcome,
) -> Iterator[Event]:
    deadline = time.monotonic() + config.deadline_s
    messages = [{"role": "user", "content": build_user_message(query.prompt, query.constraints, catalogue)}]
    outcome.violations = []
    for attempt in range(MAX_REPAIRS + 1):
        yield Event("progress", {"stage": "planning" if attempt == 0 else "repairing", "attempt": attempt + 1})
        tokens = _estimate_tokens(SYSTEM, *(m["content"] for m in messages)) + config.max_tokens
        reservation: Reservation | None = None
        try:
            reservation = quota.reserve(user_id, tokens)
            if reservation is None:
                outcome.reason = "quota_exhausted"
                return
            outcome.attempts = attempt + 1
            spent = 0
            try:
                result = complete_with_retries(
                    llm,
                    system=SYSTEM,
                    messages=messages,
                    schema=PLAN_SCHEMA,
                    max_tokens=config.max_tokens,
                    timeout=config.attempt_timeout_s,
                    deadline=deadline,
                )
                spent = result.input_tokens + result.output_tokens
                outcome.input_tokens += result.input_tokens
                outcome.output_tokens += result.output_tokens
            except LlmError as exc:
                spent = exc.input_tokens + exc.output_tokens
                outcome.input_tokens += exc.input_tokens
                outcome.output_tokens += exc.output_tokens
                log.warning("LLM planning failed: %s (%s)", exc.kind, exc)
                outcome.reason = f"llm_{exc.kind}"
                return
            finally:
                quota.settle(reservation, spent)
        except QuotaUnavailableError as exc:
            # Not metered, so not spent: plan without the model.
            log.warning("Token quota unavailable: %s", exc)
            outcome.reason = "quota_unavailable"
            return

        yield Event("progress", {"stage": "validating", "attempt": attempt + 1})
        items, violations = parse_plan(result.text)
        if not violations:
            violations = validate(items, query.constraints, catalogue)
        if not violations:
            outcome.items = items
            return
        outcome.violations.extend(v.rule for v in violations)
        log.info("Plan failed validation on attempt %d: %s", attempt + 1, sorted({v.rule for v in violations}))
        messages = [
            *messages,
            {"role": "assistant", "content": result.text},
            {"role": "user", "content": repair_message(violations)},
        ]
    outcome.reason = "validation_failed"


def present(
    items: list[ProposedItem], query: ItineraryQuery, catalogue: Catalogue, parsed: ParsedQuery, outcome: _LlmOutcome
) -> dict:
    """The answer, built from the catalogue. Names, prices, times and journeys are ours, whoever chose the
    departures."""
    constraints = query.constraints
    chosen = sorted((catalogue.departures[item.departure_id] for item in items), key=lambda d: d.starts_at)
    days: dict[date, list[dict]] = {}
    total = Decimal(0)
    previous: Departure | None = None
    for departure in chosen:
        cost = departure.price * constraints.party_size
        total += cost
        days.setdefault(departure.local_date, []).append(
            {
                "tourId": departure.tour_id,
                "departureId": departure.availability_id,
                "name": departure.name,
                "startTime": departure.starts_at.astimezone(WINDHOEK).isoformat(),
                "durationMinutes": departure.duration_minutes,
                "pricePerPerson": str(departure.price),
                "cost": str(cost),
                "travelMinutesFromPrevious": (
                    catalogue.travel_minutes(previous.tour_id, departure.tour_id) if previous else 0
                ),
            }
        )
        previous = departure
    path = "deterministic" if outcome.reason else ("llm" if outcome.attempts == 1 else "llm_repaired")
    return {
        "path": path,
        "fallbackReason": outcome.reason,
        "llmAttempts": outcome.attempts,
        "violationsSeen": outcome.violations or [],
        "startDate": constraints.start_date.isoformat(),
        "days": [{"date": day.isoformat(), "items": entries} for day, entries in sorted(days.items())],
        "totalCost": str(total),
        "budget": str(constraints.budget),
        "currency": "NAD",
        "partySize": constraints.party_size,
        "understood": parsed.understood,
        "tokens": {"input": outcome.input_tokens, "output": outcome.output_tokens},
    }


def plan_itinerary(
    query: ItineraryQuery,
    *,
    user_id: str,
    session_factory: Callable[[], Session],
    embedder: Embedder,
    parser: QueryParser,
    llm: LLMClient | None,
    quota: TokenQuota | None,
    config: PlannerConfig = PlannerConfig(),
) -> Iterator[Event]:
    yield Event("progress", {"stage": "parsing"})
    parsed = parser.parse(query.prompt)
    yield Event("progress", {"stage": "retrieving"})
    catalogue = _retrieve(session_factory, embedder, query, parsed)
    yield Event("progress", {"stage": "retrieved", "tours": len(catalogue.tour_ids)})

    outcome = _LlmOutcome(reason=None if llm else "no_provider")
    if llm is not None and quota is not None and not catalogue.departures:
        outcome.reason = "nothing_to_plan"  # no tokens spent on an empty catalogue
    elif llm is not None and quota is not None:
        yield from _plan_with_llm(query, catalogue, user_id, llm, quota, config, outcome)
    elif llm is not None:
        outcome.reason = "quota_unavailable"

    items = outcome.items
    if items is None:
        yield Event("progress", {"stage": "fallback", "reason": outcome.reason})
        items = deterministic_plan(query.constraints, catalogue)
        problems: list[Violation] = validate(items, query.constraints, catalogue)
        if problems:
            # A bug in the planner, not a state of the world. An empty plan is valid; an invalid one is a lie.
            log.error("The deterministic planner broke its own rules: %s", [p.rule for p in problems])
            items = []
    plan = present(items, query, catalogue, parsed, outcome)
    _plans.add(1, {"path": plan["path"], "fallback.reason": plan["fallbackReason"] or "none"})
    yield Event("plan", plan)
