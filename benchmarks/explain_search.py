#!/usr/bin/env python3
"""Capture EXPLAIN (ANALYZE, BUFFERS) for the search queries, with and without each index, on tours-db.

The queries are the SQL TourSearchRepository renders (captured from tours-db's statement log), with the
bind values inlined. Every variant runs inside BEGIN; DROP INDEX / CREATE INDEX ...; EXPLAIN ...; ROLLBACK,
so the schema is never really changed. Each query runs once to warm the buffer cache, then RUNS times;
the plan of the median run is kept.

Writes benchmarks/out/explain/*.txt and prints a summary table. Needs the stack running and the 100k seed
(scripts/seed_tours.sql). Standard library only.

    python benchmarks/explain_search.py
"""
from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "benchmarks"))
from run_booking_contention import env_file_value  # noqa: E402

OUT = ROOT / "benchmarks" / "out" / "explain"
MARKER = "=== measured run ==="

SELECT = """select "t"."id", "t"."name", "t"."description", "t"."price", "t"."currency", "t"."category",
  "t"."difficulty", "t"."duration_minutes", "t"."avg_rating", "t"."rating_count", "t"."created_at",
  ST_Y("t"."location") as "latitude", ST_X("t"."location") as "longitude\""""


def point(lon: float, lat: float) -> str:
    return (f"CAST(ST_SetSRID(ST_MakePoint(cast({lon} as double precision), cast({lat} as double precision)), 4326)"
            " AS geography)")


WINDHOEK = point(17.0658, -22.5609)
DISTANCE = f'CAST("t"."location" AS geography) <-> {WINDHOEK}'
DEPARTURE_WINDOW = """exists (select 1 as "one" from "public"."tour_departure_summary" as "s"
    where ("s"."tour_id" = "t"."id" and "s"."starts_at" > cast(current_timestamp as timestamp with time zone)
      and "s"."slots_left" >= 2
      and "s"."starts_at" >= cast(date_trunc('day', now()) + interval '20 days' as timestamp(6) with time zone)
      and "s"."starts_at" < cast(date_trunc('day', now()) + interval '34 days' as timestamp(6) with time zone)))"""



def nearest_first(where: str) -> str:
    """TourSearchRepository.nearestFirst: KNN candidates, plus every row tied at the boundary distance."""
    cols = f"""{SELECT}, {DISTANCE} as "sort_key", {DISTANCE} as "distance_meters\""""
    return f"""with "candidates" as ({cols}
  from "public"."tours" as "t" where {where} order by {DISTANCE} asc fetch next 21 rows only)
select * from (select * from "candidates"
  union
  ({cols} from "public"."tours" as "t" where {where}
    and ST_DWithin(CAST("t"."location" AS geography), {WINDHOEK}, (select max("candidates"."sort_key") from "candidates") + 1, false)
    and {DISTANCE} = (select max("candidates"."sort_key") from "candidates"))) as "nearest"
order by "nearest"."sort_key" asc, "nearest"."id" asc fetch next 21 rows only"""


# The SQL TourSearchRepository renders (captured from tours-db's statement log), bind values inlined.
QUERIES: dict[str, str] = {
    # 1. radius + price, nearest first: the two-step KNN query TourSearchRepository runs
    "radius_price_distance": nearest_first(
        f"""(ST_DWithin(CAST("t"."location" AS geography), {WINDHOEK}, cast(200000.0 as double precision), false))
  and "t"."price" <= 1500"""),
    # 1b. the same as one statement ordered by (distance, id): no KNN, every candidate is sorted
    "radius_price_distance_one_statement": f"""{SELECT}, {DISTANCE} as "sort_key", {DISTANCE} as "distance_meters"
from "public"."tours" as "t"
where ((ST_DWithin(CAST("t"."location" AS geography), {WINDHOEK}, cast(200000.0 as double precision), false))
  and "t"."price" <= 1500)
order by {DISTANCE} asc, "t"."id" asc fetch next 21 rows only""",
    # 1c. nearest first from a point with no radius, two-step KNN
    "point_only_distance": nearest_first("true"),
    # 1d. the same as one statement: a sort of all 100k tours
    "point_only_distance_one_statement": f"""{SELECT}, {DISTANCE} as "sort_key", {DISTANCE} as "distance_meters"
from "public"."tours" as "t"
order by {DISTANCE} asc, "t"."id" asc fetch next 21 rows only""",
    # 2. full text + category + rating, best match first
    "text_category_rating": f"""{SELECT},
  ts_rank_cd("t"."search_vector", websearch_to_tsquery('english', 'game drive')) as "sort_key"
from "public"."tours" as "t"
where (("t"."search_vector" @@ websearch_to_tsquery('english', 'game drive'))
  and "t"."category" in ('SAFARI', 'WILDLIFE') and "t"."avg_rating" >= 4.5)
order by ts_rank_cd("t"."search_vector", websearch_to_tsquery('english', 'game drive')) desc, "t"."id" desc
fetch next 21 rows only""",
    # 3. bbox (Namibia) + a departure with 2 free seats in a two-week window, cheapest first
    "bbox_dates_price": f"""{SELECT}, "t"."price" as "sort_key"
from "public"."tours" as "t"
where (("t"."location" && ST_MakeEnvelope(cast(11.72 as double precision), cast(-28.97 as double precision),
    cast(25.26 as double precision), cast(-16.96 as double precision), 4326))
  and {DEPARTURE_WINDOW})
order by "t"."price" asc, "t"."id" asc fetch next 21 rows only""",
    # 3b. a map viewport (the Namib around Sossusvlei, about 1% of the catalogue), best rated first
    "viewport_rating": f"""{SELECT}, "t"."avg_rating" as "sort_key"
from "public"."tours" as "t"
where ("t"."location" && ST_MakeEnvelope(cast(15.0 as double precision), cast(-25.2 as double precision),
    cast(15.8 as double precision), cast(-24.3 as double precision), 4326))
order by "t"."avg_rating" desc, "t"."id" desc fetch next 21 rows only""",
    # 3c. a sparse viewport (central Kalahari, away from every hub), cheapest first
    "sparse_viewport_price": f"""{SELECT}, "t"."price" as "sort_key"
from "public"."tours" as "t"
where ("t"."location" && ST_MakeEnvelope(cast(21.0 as double precision), cast(-24.0 as double precision),
    cast(22.0 as double precision), cast(-23.0 as double precision), 4326))
order by "t"."price" asc, "t"."id" asc fetch next 21 rows only""",
    # 4a. deep page by keyset: after price 4000.00 / id 50000 (about page 1000 at size 20)
    "deep_keyset": f"""{SELECT}, "t"."price" as "sort_key"
from "public"."tours" as "t"
where ("t"."price", "t"."id") > (4000.00, 50000)
order by "t"."price" asc, "t"."id" asc fetch next 21 rows only""",
    # 4b. the same depth by OFFSET
    "deep_offset": f"""{SELECT}, "t"."price" as "sort_key"
from "public"."tours" as "t"
order by "t"."price" asc, "t"."id" asc offset 20000 rows fetch next 21 rows only""",
    # 6. best rated first, no filters
    "rating": f"""{SELECT}, "t"."avg_rating" as "sort_key"
from "public"."tours" as "t"
order by "t"."avg_rating" desc, "t"."id" desc fetch next 21 rows only""",
    # 5. newest first, no filters (the default browse)
    "newest": f"""{SELECT}, "t"."created_at" as "sort_key"
from "public"."tours" as "t"
order by "t"."created_at" desc, "t"."id" desc fetch next 21 rows only""",
}

# Candidate indexes, created only inside the rolled-back measurement transaction.
GEOM = "CREATE INDEX idx_tours_location_geom ON tours USING GIST (location)"
PRICE = "CREATE INDEX idx_tours_price_id ON tours (price, id)"
CREATED = "CREATE INDEX idx_tours_created_id ON tours (created_at, id)"
RATING = "CREATE INDEX idx_tours_rating_id ON tours (avg_rating, id)"
OPEN_DEPARTURES = ("CREATE INDEX idx_departure_summary_open ON tour_departure_summary (tour_id, starts_at) "
                   "WHERE slots_left > 0")

# query -> [(label, indexes to drop, indexes to create)]. The first entry is the schema as migrated.
VARIANTS: dict[str, list[tuple[str, list[str], list[str]]]] = {
    "radius_price_distance": [("V1-V3", [], []), ("without GiST (geography)", ["idx_tours_location_geog"], [])],
    "point_only_distance": [("V1-V3", [], []), ("without GiST (geography)", ["idx_tours_location_geog"], [])],
    "text_category_rating": [("V1-V3", [], []), ("without GIN (search_vector)", ["idx_tours_search_vector"], [])],
    "bbox_dates_price": [
        ("V1-V3", [], []),
        ("+ GiST (geometry)", [], [GEOM]),
        ("+ (price, id)", [], [PRICE]),
        ("+ GiST (geometry) + (price, id)", [], [GEOM, PRICE]),
        ("without (tour_id, starts_at) INCLUDE slots_left", ["idx_departure_summary_tour_starts"], []),
        ("(tour_id, starts_at) WHERE slots_left > 0 instead", ["idx_departure_summary_tour_starts"], [OPEN_DEPARTURES]),
    ],
    "viewport_rating": [
        ("V1-V3", [], []),
        ("+ (avg_rating, id)", [], [RATING]),
        ("+ GiST (geometry)", [], [GEOM]),
        ("+ GiST (geometry) + (avg_rating, id)", [], [GEOM, RATING]),
    ],
    "sparse_viewport_price": [
        ("V1-V3", [], []),
        ("+ (price, id)", [], [PRICE]),
        ("+ GiST (geometry) + (price, id)", [], [GEOM, PRICE]),
    ],
    "deep_keyset": [("V1-V3", [], []), ("+ (price, id)", [], [PRICE])],
    "deep_offset": [("V1-V3", [], []), ("+ (price, id)", [], [PRICE])],
    "newest": [("V1-V3", [], []), ("+ (created_at, id)", [], [CREATED])],
    "rating": [("V1-V3", [], []), ("+ (avg_rating, id)", [], [RATING])],
}


def psql(sql: str) -> str:
    user = env_file_value("TOURS_DB_USER") or "tours"
    result = subprocess.run(
        ["docker", "compose", "exec", "-T", "tours-db", "psql", "-U", user, "-d", "tours", "-v", "ON_ERROR_STOP=1",
         "-X", "-q", "-t", "-A"],
        cwd=ROOT, input=sql, capture_output=True, text=True, check=False,
    )  # fmt: skip
    if result.returncode != 0:
        raise SystemExit(f"psql failed:\n{result.stderr}\n--- sql ---\n{sql}")
    return result.stdout


RUNS = 5


def explain(query: str, drop: list[str], create: list[str] = ()) -> str:
    """The plan of the median of RUNS measured runs (after one warm-up), with indexes dropped or created
    inside a transaction that is rolled back."""
    changes = "".join(f"DROP INDEX IF EXISTS {name};\n" for name in drop)
    changes += "".join(f"{ddl};\n" for ddl in create)
    if create:
        changes += "ANALYZE tours; ANALYZE tour_departure_summary;\n"
    statement = f"EXPLAIN (ANALYZE, BUFFERS) {query};\n"
    runs = "".join(f"\\echo {MARKER}\n{statement}" for _ in range(RUNS))
    output = psql(f"BEGIN;\n{changes}{statement}{runs}ROLLBACK;\n")
    plans = [plan.strip() for plan in output.split(MARKER)[1:]]
    return sorted(plans, key=execution_ms)[len(plans) // 2]


def execution_ms(plan: str) -> float:
    match = re.search(r"Execution Time: ([0-9.]+) ms", plan)
    return float(match.group(1)) if match else float("nan")


def buffers(plan: str) -> str:
    match = re.search(r"Buffers: (shared[^\n]*)", plan)
    return match.group(1) if match else ""


def main() -> None:
    only = set(sys.argv[1:])  # optional: query names to run
    OUT.mkdir(parents=True, exist_ok=True)
    psql("ANALYZE tours; ANALYZE tour_departure_summary;")
    rows = []
    for name, query in QUERIES.items():
        if only and name not in only:
            continue
        for label, drop, create in VARIANTS.get(name, [("V1-V3", [], [])]):
            plan = explain(query, drop, create)
            slug = re.sub(r"[^a-z0-9]+", "_", label.lower()).strip("_")
            (OUT / f"{name}__{slug}.txt").write_text(plan, encoding="utf-8")
            rows.append((name, label, execution_ms(plan), buffers(plan)))
            print(f"{name} [{label}] {execution_ms(plan):.2f} ms", file=sys.stderr)
    print(f"| Query | Indexes | Execution, median of {RUNS} (ms) | Buffers (top node) |")
    print("| --- | --- | ---: | --- |")
    for name, label, ms, buf in rows:
        print(f"| {name} | {label} | {ms:.2f} | {buf} |")
    print(f"\nplans written to {OUT.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
