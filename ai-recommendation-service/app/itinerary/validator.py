"""The rules a plan must pass, whoever wrote it (ADR 0021).

The catalogue here is what retrieval returned for this request, so "a tour that isn't in the catalogue" and "a tour
that wasn't retrieved" are the same defect. Claims in a plan (price, start, duration) are checked against it too: a
model that misreports a price has misread the catalogue, and the repair loop should hear about it. What the traveller
finally sees comes from the catalogue, never from the claims.
"""

from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

WINDHOEK = ZoneInfo("Africa/Windhoek")

# Straight-line kilometres to minutes on the road. 60 km/h is deliberately below Namibia's 100 km/h gravel limit: the
# distance is as the crow flies, roads aren't, and most of the network is unsealed. So one kilometre costs one minute.
AVERAGE_ROAD_SPEED_KMH = 60


@dataclass(frozen=True)
class Departure:
    availability_id: int
    tour_id: int
    name: str
    description: str
    starts_at: datetime
    duration_minutes: int
    price: Decimal
    slots_left: int
    rank: int = 0  # position in the retrieval results: lower is more relevant

    @property
    def ends_at(self) -> datetime:
        return self.starts_at + timedelta(minutes=self.duration_minutes)

    @property
    def local_date(self) -> date:
        return self.starts_at.astimezone(WINDHOEK).date()


@dataclass(frozen=True)
class Constraints:
    start_date: date
    days: int
    party_size: int
    budget: Decimal

    @property
    def end_date(self) -> date:
        return self.start_date + timedelta(days=self.days - 1)


@dataclass
class Catalogue:
    departures: dict[int, Departure] = field(default_factory=dict)
    distances_km: dict[tuple[int, int], float] = field(default_factory=dict)

    @property
    def tour_ids(self) -> set[int]:
        return {d.tour_id for d in self.departures.values()}

    def travel_minutes(self, from_tour: int, to_tour: int) -> int:
        if from_tour == to_tour:
            return 0
        key = (min(from_tour, to_tour), max(from_tour, to_tour))
        return round(self.distances_km[key] / AVERAGE_ROAD_SPEED_KMH * 60)


@dataclass(frozen=True)
class ProposedItem:
    day: date
    tour_id: int
    departure_id: int
    start_time: datetime
    duration_minutes: int
    price: Decimal


@dataclass(frozen=True)
class Violation:
    rule: str
    message: str


def fits_on_its_own(departure: Departure, constraints: Constraints) -> bool:
    """Could this departure be a whole plan? Used to tell an empty plan that had to be from one that didn't."""
    return (
        constraints.start_date <= departure.local_date <= constraints.end_date
        and departure.slots_left >= constraints.party_size
        and departure.price * constraints.party_size <= constraints.budget
        and departure.ends_at.astimezone(WINDHOEK).date() <= constraints.end_date
    )


def validate(items: list[ProposedItem], constraints: Constraints, catalogue: Catalogue) -> list[Violation]:
    violations: list[Violation] = []
    if not items and any(fits_on_its_own(d, constraints) for d in catalogue.departures.values()):
        violations.append(Violation("empty_plan", "The plan is empty, but the catalogue has departures that fit."))

    known: list[Departure] = []
    seen_tours: set[int] = set()
    for item in items:
        where = f"tour {item.tour_id}, departure {item.departure_id}"
        if item.tour_id not in catalogue.tour_ids:
            violations.append(Violation("unknown_tour", f"Tour {item.tour_id} is not in the catalogue you were given."))
            continue
        departure = catalogue.departures.get(item.departure_id)
        if departure is None:
            violations.append(
                Violation("unknown_departure", f"Departure {item.departure_id} is not in the catalogue you were given.")
            )
            continue
        if departure.tour_id != item.tour_id:
            violations.append(
                Violation(
                    "departure_of_other_tour", f"Departure {item.departure_id} belongs to tour {departure.tour_id}."
                )
            )
            continue
        known.append(departure)
        if item.tour_id in seen_tours:
            violations.append(Violation("repeated_tour", f"Tour {item.tour_id} appears more than once."))
        seen_tours.add(item.tour_id)
        if not constraints.start_date <= departure.local_date <= constraints.end_date:
            violations.append(
                Violation("outside_trip", f"{where} runs on {departure.local_date}, outside the trip dates.")
            )
        elif departure.ends_at.astimezone(WINDHOEK).date() > constraints.end_date:
            violations.append(Violation("ends_after_trip", f"{where} ends after the last day of the trip."))
        if item.day != departure.local_date:
            violations.append(Violation("wrong_day", f"{where} runs on {departure.local_date}, not on {item.day}."))
        if departure.slots_left < constraints.party_size:
            violations.append(
                Violation("party_too_large", f"{where} has {departure.slots_left} seats, for {constraints.party_size}.")
            )
        if item.price != departure.price:
            violations.append(
                Violation("wrong_price", f"{where} costs {departure.price} per person, not {item.price}.")
            )
        if item.start_time != departure.starts_at:
            violations.append(
                Violation("wrong_start", f"{where} starts at {departure.starts_at.isoformat()}, not {item.start_time}.")
            )
        if item.duration_minutes != departure.duration_minutes:
            violations.append(
                Violation(
                    "wrong_duration",
                    f"{where} lasts {departure.duration_minutes} minutes, not {item.duration_minutes}.",
                )
            )

    total = sum((d.price * constraints.party_size for d in known), Decimal(0))
    if total > constraints.budget:
        violations.append(Violation("over_budget", f"The plan costs {total}, over the budget of {constraints.budget}."))

    ordered = sorted(known, key=lambda d: d.starts_at)
    for previous, following in zip(ordered, ordered[1:], strict=False):
        gap = (following.starts_at - previous.ends_at).total_seconds() / 60
        pair = f"tour {previous.tour_id} and tour {following.tour_id}"
        if gap < 0:
            violations.append(Violation("overlap", f"{pair} overlap."))
            continue
        needed = catalogue.travel_minutes(previous.tour_id, following.tour_id)
        if needed > gap:
            violations.append(
                Violation(
                    "travel_infeasible", f"{pair} are {needed} minutes apart by road, with {gap:.0f} between them."
                )
            )
    return violations
