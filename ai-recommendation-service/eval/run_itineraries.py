#!/usr/bin/env python3
"""Itinerary evaluation: 40 prompts through the real pipeline, each result re-checked independently of the validator.

Run from ai-recommendation-service, with DATABASE_URL on a database eval.run_eval has filled:

    python -m eval.run_itineraries --model minilm-l6 --provider none fake:good fake:repair --out out.json

The Fake provider's results describe this pipeline's paths and overhead, by construction: they say nothing about a
model. A real provider is only ever reported after it has actually been run (LLM_PROVIDER and its settings in the
environment, then --provider configured).
"""

import argparse
import json
import random
import time
from datetime import UTC, date, datetime, timedelta
from datetime import time as day_time
from decimal import Decimal
from pathlib import Path
from zoneinfo import ZoneInfo

from sqlalchemy import text

from app import read_model
from app.config import get_settings
from app.database import SessionLocal
from app.embeddings import MODELS, OnnxEmbedder
from app.gazetteer import load_csv
from app.itinerary.service import ItineraryQuery, plan_itinerary
from app.itinerary.validator import AVERAGE_ROAD_SPEED_KMH, Constraints
from app.llm import FakeLLMClient, build_client
from app.query_parser import QueryParser
from app.quota import Reservation

from .metrics import percentile
from .run_eval import load_tours

WINDHOEK = ZoneInfo("Africa/Windhoek")
DEPARTURES_PER_TOUR = 12
HORIZON_DAYS = 120

# (prompt, days, party, budget in NAD, start offset in days). English and German, broad and narrow, generous and tight.
PROMPTS = [
    ("A relaxed week in the desert with some wildlife", 7, 2, 30000, 14),
    ("Three days of adventure on the coast near Swakopmund", 3, 2, 12000, 20),
    ("Family trip with young children, nothing too strenuous", 5, 4, 25000, 25),
    ("Culture and history around Windhoek", 3, 1, 5000, 10),
    ("Game drives in Etosha for a long weekend", 4, 2, 15000, 30),
    ("Budget backpacker looking for cheap day trips", 5, 1, 3000, 12),
    ("Honeymoon: sunsets, stargazing and quiet places", 6, 2, 40000, 40),
    ("Photography tour of dunes and dead trees", 4, 2, 18000, 18),
    ("One big day out near Walvis Bay, kayaks or dolphins", 1, 2, 3000, 16),
    ("A group of ten colleagues wants a team outing", 2, 10, 20000, 22),
    ("Hiking and climbing, I am fit and want a challenge", 7, 1, 20000, 35),
    ("Birdwatching and quiet walks", 3, 2, 8000, 28),
    ("Rock art and desert elephants in Damaraland", 5, 2, 22000, 45),
    ("Everything you have got for a very small budget", 3, 2, 500, 15),
    ("Luxury no matter the price", 5, 2, 200000, 50),
    ("Whitewater rafting and bungee in the Victoria Falls area", 4, 3, 30000, 33),
    ("A slow trip along the Skeleton Coast", 6, 2, 35000, 60),
    ("Half day tours only, under 3 hours each", 3, 2, 6000, 19),
    ("Food, beer and markets in the city", 2, 2, 4000, 11),
    ("Two weeks across the whole country", 14, 2, 80000, 24),
    ("Eine entspannte Woche in der Wüste mit Tierbeobachtung", 7, 2, 30000, 21),
    ("Drei Tage Abenteuer an der Küste bei Swakopmund", 3, 2, 12000, 27),
    ("Familienurlaub mit kleinen Kindern, nicht zu anstrengend", 5, 4, 25000, 32),
    ("Kultur und Geschichte rund um Windhoek", 3, 1, 5000, 13),
    ("Pirschfahrten im Etosha für ein langes Wochenende", 4, 2, 15000, 37),
    ("Günstige Tagesausflüge für Rucksackreisende", 5, 1, 3000, 17),
    ("Flitterwochen mit Sonnenuntergängen und Sternenhimmel", 6, 2, 40000, 42),
    ("Fototour durch Dünen und zu toten Bäumen", 4, 2, 18000, 23),
    ("Ein großer Tag bei Walfischbucht, Kajak oder Delfine", 1, 2, 3000, 26),
    ("Betriebsausflug für zehn Kollegen", 2, 10, 20000, 29),
    ("Wandern und Klettern, ich bin fit und suche eine Herausforderung", 7, 1, 20000, 38),
    ("Vogelbeobachtung und ruhige Spaziergänge", 3, 2, 8000, 31),
    ("Felskunst und Wüstenelefanten im Damaraland", 5, 2, 22000, 47),
    ("Alles was geht für ein sehr kleines Budget", 3, 2, 500, 34),
    ("Luxus, der Preis spielt keine Rolle", 5, 2, 200000, 52),
    ("Rafting und Bungee bei den Viktoriafällen", 4, 3, 30000, 36),
    ("Langsame Reise entlang der Skelettküste", 6, 2, 35000, 58),
    ("Nur Halbtagestouren, höchstens 3 Stunden", 3, 2, 6000, 41),
    ("Essen, Bier und Märkte in der Stadt", 2, 2, 4000, 43),
    ("Zwei Wochen quer durch das ganze Land", 14, 2, 80000, 44),
]


class NoQuota:
    """The token quota has its own tests. Here it would only add a Redis dependency to a measurement."""

    def reserve(self, _user, tokens):
        return Reservation("eval", tokens)

    def settle(self, _reservation, _used):
        return None


def load_departures(tours: list[dict], today: date) -> None:
    """Enough departures across the horizon to plan with: run_eval's one each is for filter queries."""
    rng = random.Random(23)
    midnight = datetime.combine(today, day_time.min, WINDHOEK)
    with SessionLocal() as db, db.begin():
        db.execute(text("TRUNCATE ai_departures"))
        availability_id = 1
        for tour in tours:
            for _ in range(DEPARTURES_PER_TOUR):
                offset = timedelta(days=rng.randint(1, HORIZON_DAYS), hours=rng.choice([6, 8, 9, 13, 16]))
                starts_at = midnight + offset
                read_model.upsert_departure(
                    db,
                    availability_id=availability_id,
                    tour_id=tour["id"],
                    starts_at=starts_at,
                    total_slots=12,
                    slots_left=rng.randint(0, 12),
                    version=1,
                )
                availability_id += 1
        read_model.analyze(db)


def independent_check(plan: dict, constraints: Constraints) -> list[str]:
    """Against the tables, with its own SQL and arithmetic. All it shares with the validator is the speed constant."""
    items = [item for day in plan["days"] for item in day["items"]]
    if not items:
        return []
    problems = []
    with SessionLocal() as db:
        rows = db.execute(
            text(
                """
                SELECT d.availability_id, d.tour_id, d.starts_at, d.slots_left, t.price, t.duration_minutes
                FROM ai_departures d JOIN ai_tours t ON t.tour_id = d.tour_id
                WHERE d.availability_id = ANY(:ids)
                """
            ),
            {"ids": [item["departureId"] for item in items]},
        ).all()
        found = {row.availability_id: row for row in rows}
        total = Decimal(0)
        for item in items:
            row = found.get(item["departureId"])
            if row is None or row.tour_id != item["tourId"]:
                problems.append("not_in_catalogue")
                continue
            if row.slots_left < constraints.party_size:
                problems.append("no_seats")
            local = row.starts_at.astimezone(WINDHOEK).date()
            if not constraints.start_date <= local <= constraints.end_date:
                problems.append("outside_dates")
            total += row.price * constraints.party_size
        if total > constraints.budget:
            problems.append("over_budget")
        known = sorted((found[i["departureId"]] for i in items if i["departureId"] in found), key=lambda r: r.starts_at)
        for before, after in zip(known, known[1:], strict=False):
            km = db.execute(
                text(
                    """
                    SELECT ST_Distance(a.location, b.location) / 1000
                    FROM ai_tours a, ai_tours b WHERE a.tour_id = :x AND b.tour_id = :y
                    """
                ),
                {"x": before.tour_id, "y": after.tour_id},
            ).scalar_one()
            ends = before.starts_at + timedelta(minutes=before.duration_minutes)
            gap = (after.starts_at - ends).total_seconds() / 60
            if gap < km / AVERAGE_ROAD_SPEED_KMH * 60 - 1:  # rounding to whole minutes
                problems.append("overlap" if gap < 0 else "travel")
    return problems


def provider_client(name: str):
    if name == "none":
        return None
    if name.startswith("fake:"):
        return FakeLLMClient(name.removeprefix("fake:"))
    if name == "configured":
        return build_client(get_settings())
    raise SystemExit(f"Unknown provider {name}")


def evaluate(name: str, embedder, parser, today: date) -> dict:
    llm = provider_client(name)
    rows = []
    for prompt, days, party, budget, offset in PROMPTS:
        constraints = Constraints(today + timedelta(days=offset), days, party, Decimal(budget))
        began = time.perf_counter()
        events = list(
            plan_itinerary(
                ItineraryQuery(prompt, constraints),
                user_id="eval",
                session_factory=SessionLocal,
                embedder=embedder,
                parser=parser,
                llm=llm,
                quota=NoQuota() if llm else None,
            )
        )
        elapsed_ms = (time.perf_counter() - began) * 1000
        plan = events[-1].data
        items = [item for day in plan["days"] for item in day["items"]]
        rows.append(
            {
                "prompt": prompt,
                "path": plan["path"],
                "reason": plan["fallbackReason"],
                "attempts": plan["llmAttempts"],
                "problems": independent_check(plan, constraints),
                "items": len(items),
                "days_covered": len(plan["days"]),
                "days": days,
                "budget_used": float(Decimal(plan["totalCost"]) / constraints.budget),
                "ms": elapsed_ms,
                "tokens": plan["tokens"]["input"] + plan["tokens"]["output"],
            }
        )
    n = len(rows)
    with_llm = [r for r in rows if r["attempts"]]
    share = lambda path: sum(1 for r in with_llm if r["path"] == path) / len(with_llm) if with_llm else None  # noqa: E731
    return {
        "provider": name,
        "plans": n,
        "constraint_satisfaction": sum(1 for r in rows if not r["problems"]) / n,
        "problems": sorted({p for r in rows for p in r["problems"]}),
        "empty_plans": sum(1 for r in rows if not r["items"]),
        "first_pass_valid": share("llm"),
        "repair_rate": share("llm_repaired"),
        "fallback_rate": share("deterministic"),
        "ms_p50": percentile([r["ms"] for r in rows], 0.5),
        "ms_p95": percentile([r["ms"] for r in rows], 0.95),
        "tokens_per_plan": sum(r["tokens"] for r in rows) / n,
        "items_per_plan": sum(r["items"] for r in rows) / n,
        "day_coverage": sum(r["days_covered"] / r["days"] for r in rows) / n,
        "budget_used": sum(r["budget_used"] for r in rows) / n,
        "rows": rows,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--model", required=True, choices=list(MODELS))
    parser.add_argument("--provider", nargs="+", default=["none"])
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()

    today = datetime.now(UTC).astimezone(WINDHOEK).date()
    load_departures(load_tours(), today)
    embedder = OnnxEmbedder(args.model, MODELS[args.model])
    embedder.warm_up()
    query_parser = QueryParser(load_csv())
    results = [evaluate(name, embedder, query_parser, today) for name in args.provider]
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(results, indent=1), encoding="utf-8")
    for result in results:
        print({key: value for key, value in result.items() if key != "rows"})


if __name__ == "__main__":
    main()
