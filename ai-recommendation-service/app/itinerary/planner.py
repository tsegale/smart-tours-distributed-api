"""The deterministic planner: what the endpoint falls back to, and all it does with no LLM configured.

Greedy, in rounds: each round offers every day of the trip its most relevant departure that still fits, so a long trip
gets something on each day before any day gets a second item. It respects every validator rule by construction, and
the service validates its output anyway.
"""

from collections import defaultdict
from datetime import date, timedelta
from decimal import Decimal

from .validator import Catalogue, Constraints, Departure, ProposedItem, fits_on_its_own

MAX_ITEMS_PER_DAY = 2


def _can_insert(candidate: Departure, chosen: list[Departure], catalogue: Catalogue) -> bool:
    """Does it fit in time, with the road journey from the item before it and to the item after it?"""
    for other in chosen:
        if other.starts_at <= candidate.starts_at:
            gap = (candidate.starts_at - other.ends_at).total_seconds() / 60
            if gap < catalogue.travel_minutes(other.tour_id, candidate.tour_id):
                return False
        else:
            gap = (other.starts_at - candidate.ends_at).total_seconds() / 60
            if gap < catalogue.travel_minutes(candidate.tour_id, other.tour_id):
                return False
    return True


def deterministic_plan(
    constraints: Constraints, catalogue: Catalogue, max_items_per_day: int = MAX_ITEMS_PER_DAY
) -> list[ProposedItem]:
    by_day: dict[date, list[Departure]] = defaultdict(list)
    for departure in catalogue.departures.values():
        if fits_on_its_own(departure, constraints):
            by_day[departure.local_date].append(departure)
    for options in by_day.values():
        options.sort(key=lambda d: (d.rank, d.starts_at, d.availability_id))

    chosen: list[Departure] = []
    used_tours: set[int] = set()
    spent = Decimal(0)
    days = [constraints.start_date + timedelta(days=offset) for offset in range(constraints.days)]
    for round_number in range(max_items_per_day):
        for day in days:
            if sum(1 for d in chosen if d.local_date == day) > round_number:
                continue
            for option in by_day[day]:
                cost = option.price * constraints.party_size
                if option.tour_id in used_tours or spent + cost > constraints.budget:
                    continue
                if not _can_insert(option, chosen, catalogue):
                    continue
                chosen.append(option)
                used_tours.add(option.tour_id)
                spent += cost
                break

    chosen.sort(key=lambda d: d.starts_at)
    return [
        ProposedItem(
            day=d.local_date,
            tour_id=d.tour_id,
            departure_id=d.availability_id,
            start_time=d.starts_at,
            duration_minutes=d.duration_minutes,
            price=d.price,
        )
        for d in chosen
    ]
