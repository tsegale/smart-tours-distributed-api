"""Queries whose relevance follows from the generated attributes. Over the whole catalogue
(docs/benchmarks/search-quality.md).

Phrased in other words than the fragments the composed tours are built from: a query that copied a fragment would only
prove that identical text matches. Grades: 3 the right activity and every constraint, 2 the right activity in the
wider area, 1 the same category and every constraint, 0 otherwise. Machine-made by rule: not a human judgment.
"""

import math

ACTIVITY_QUERIES = {
    "dune_climb": ("hiking up huge sand mountains at daybreak", "große Sandberge im Morgengrauen hinaufsteigen"),
    "scenic_flight": ("see the landscape from a small plane", "Landschaft aus dem Flugzeug sehen"),
    "game_drive": ("spot big animals from a vehicle", "große Tiere vom Auto aus beobachten"),
    "rhino_tracking": ("follow rhinos on foot", "Nashörnern zu Fuß folgen"),
    "kayak": ("paddling with seals", "mit Robben paddeln"),
    "boat_cruise": ("relaxing evening on a boat", "entspannter Abend auf dem Boot"),
    "canyon_hike": ("strenuous mountain trek for fit walkers", "anstrengende Bergwanderung für Trainierte"),
    "rock_art": ("prehistoric carvings on stone", "prähistorische Zeichnungen auf Stein"),
    "village_visit": ("meet local families at home", "einheimische Familien zu Hause treffen"),
    "town_walk": ("history tour on foot through town", "Geschichte zu Fuß durch die Stadt"),
    "astro": ("look at planets and galaxies at night", "nachts Planeten und Galaxien ansehen"),
    "fishing": ("catching fish with a rod", "Fische mit der Rute fangen"),
    "quad": ("motorised off-road thrill in the sand", "motorisierter Spaß im Sand"),
    "food": ("try local dishes and drinks", "einheimische Gerichte und Getränke probieren"),
    "birding": ("watching rare birds", "seltene Vögel beobachten"),
    "photo": ("learn to take better pictures", "bessere Bilder machen lernen"),
}
AREAS = {
    "dune_climb": "Sossusvlei", "scenic_flight": "Swakopmund", "game_drive": "Etosha", "rhino_tracking": "Damaraland",
    "kayak": "Walvis Bay", "boat_cruise": "Kasane", "canyon_hike": "Fish River Canyon", "rock_art": "Twyfelfontein",
    "village_visit": "Opuwo", "town_walk": "Windhoek", "astro": "Sossusvlei", "fishing": "Katima Mulilo",
    "quad": "Swakopmund", "food": "Swakopmund", "birding": "Maun", "photo": "Kolmanskop",
}  # fmt: skip
AREA_POINTS = {
    "Sossusvlei": (-24.733, 15.293), "Swakopmund": (-22.678, 14.527), "Etosha": (-18.855, 16.329),
    "Damaraland": (-20.35, 14.3), "Walvis Bay": (-22.957, 14.508), "Kasane": (-17.817, 25.15),
    "Fish River Canyon": (-27.583, 17.583), "Twyfelfontein": (-20.595, 14.373), "Opuwo": (-18.061, 13.84),
    "Windhoek": (-22.559, 17.083), "Katima Mulilo": (-17.5, 24.267), "Maun": (-19.983, 23.417),
    "Kolmanskop": (-26.705, 15.231),
}  # fmt: skip
RADIUS_KM = 100.0


def distance_km(a: tuple[float, float], b: tuple[float, float]) -> float:
    lat1, lon1, lat2, lon2 = map(math.radians, (*a, *b))
    h = math.sin((lat2 - lat1) / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin((lon2 - lon1) / 2) ** 2
    return 6371 * 2 * math.asin(math.sqrt(h))


def _judge(tours: list[dict], activity: str, area: str | None, max_price: int | None, family: bool) -> dict[int, int]:
    category = next(t["category"] for t in tours if t["activity"] == activity)
    point = AREA_POINTS.get(area) if area else None
    judgments = {}
    for tour in tours:
        if tour["authored"]:
            continue  # judged in the curated set, not here
        if max_price is not None and tour["price"] > max_price:
            continue
        if family and not tour["familyFriendly"]:
            continue
        near = point is None or distance_km(point, (tour["latitude"], tour["longitude"])) <= RADIUS_KM
        if tour["activity"] == activity and near:
            judgments[tour["id"]] = 3
        elif tour["activity"] == activity and point is not None:
            judgments[tour["id"]] = 2
        elif tour["category"] == category and near:
            judgments[tour["id"]] = 1
    return judgments


def build(tours: list[dict]) -> list[dict]:
    queries = []

    def add(text, language, shape, activity, area=None, max_price=None, family=False):
        judgments = _judge(tours, activity, area, max_price, family)
        if any(grade >= 2 for grade in judgments.values()):
            queries.append(
                {"id": f"rule-{len(queries)}", "q": text, "language": language, "shape": shape, "judgments": judgments}
            )

    for activity, (english, german) in ACTIVITY_QUERIES.items():
        area = AREAS[activity]
        add(english, "en", "vague", activity)
        add(german, "de", "vague", activity)
        add(f"{english} near {area}", "en", "geo", activity, area=area)
        add(f"{german} bei {area}", "de", "geo", activity, area=area)
        cheap = sorted(t["price"] for t in tours if t["activity"] == activity)
        limit = cheap[len(cheap) // 2]
        add(f"{english} under N$ {limit}", "en", "price", activity, max_price=limit)
        add(f"{german} unter {limit} NAD", "de", "price", activity, max_price=limit)
    for activity in ("boat_cruise", "game_drive", "astro", "town_walk", "birding"):
        english, german = ACTIVITY_QUERIES[activity]
        add(f"{english}, suitable for young children", "en", "family", activity, family=True)
        add(f"{german}, geeignet für kleine Kinder", "de", "family", activity, family=True)
    return queries
