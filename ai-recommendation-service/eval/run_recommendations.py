#!/usr/bin/env python3
"""Offline evaluation of personalised recommendations on a generated interaction log, with a time-based split.

THE INTERACTIONS ARE SYNTHETIC. Simulated travellers prefer certain categories and book accordingly, with a pull
towards popular tours. So this shows the ranking code finds a planted signal better or worse than its baselines; it
can't show what real travellers would do. The embeddings see only tour text, not the labels the simulation chose by.

    python -m eval.run_recommendations --model minilm-l6 --out eval/results/recommendations.json

DATABASE_URL points at the database eval.run_eval filled: this reads the tours' vectors from it.
"""

import argparse
import json
import random
from datetime import UTC, datetime, timedelta
from pathlib import Path

import numpy as np
from sqlalchemy import text

from app.database import SessionLocal
from app.embeddings import MODELS

from .metrics import bootstrap_ci, ndcg_at
from .run_eval import load_tours

SEED = 37
USERS = 500
SPAN_DAYS = 540
SPLIT_DAYS = 90  # the last three months are the test period
K = 10
FUSION_CANDIDATES = 500


def generate_log(tours: list[dict], rng: random.Random) -> list[dict]:
    """(user, tour, when, rating). Taste: one or two categories. Each booking: 70% taste, 30% whatever is popular."""
    categories = sorted({t["category"] for t in tours})
    by_category: dict[str, list[dict]] = {c: [] for c in categories}
    for tour in tours:
        by_category[tour["category"]].append(tour)
    # A long tail: popularity by a random permutation, not by id, so that nothing else correlates with it.
    order = list(range(len(tours)))
    rng.shuffle(order)
    popularity = {tours[i]["id"]: 1 / (position + 1) ** 0.8 for position, i in enumerate(order)}
    log = []
    start = datetime(2025, 1, 1, tzinfo=UTC)
    for user in range(USERS):
        taste = rng.sample(categories, rng.choice([1, 1, 2]))
        pool = [t for c in taste for t in by_category[c]]
        pool_weights = [popularity[t["id"]] ** 0.5 for t in pool]
        all_weights = [popularity[t["id"]] for t in tours]
        seen: set[int] = set()
        for _ in range(rng.randint(3, 14)):
            if rng.random() < 0.7:
                tour = rng.choices(pool, pool_weights)[0]
                rating = rng.choice([4, 4, 5, 5, 3])
            else:
                tour = rng.choices(tours, all_weights)[0]
                rating = rng.choice([2, 3, 3, 4, 5])
            if tour["id"] in seen:
                continue
            seen.add(tour["id"])
            when = start + timedelta(days=rng.uniform(0, SPAN_DAYS))
            log.append({"user": user, "tour": tour["id"], "when": when, "rating": rating, "taste": taste})
    return sorted(log, key=lambda row: row["when"])


def load_matrix(model: str) -> tuple[list[int], np.ndarray]:
    with SessionLocal() as db:
        rows = db.execute(
            text("SELECT tour_id, embedding::text FROM tour_embeddings WHERE model = :m ORDER BY tour_id"), {"m": model}
        ).all()
    return [row[0] for row in rows], np.array([json.loads(row[1]) for row in rows], dtype=np.float32)


def weights(history: list[dict], now: datetime, half_life_days: float) -> np.ndarray:
    values = []
    for row in history:
        weight = 0.0 if row["rating"] <= 2 else (2.0 if row["rating"] >= 4 else 1.0)
        values.append(weight * 0.5 ** ((now - row["when"]).total_seconds() / 86400 / half_life_days))
    return np.array(values, dtype=np.float32)


def mmr_rerank(relevance: dict[int, float], matrix: np.ndarray, k: int, lambda_: float = 0.7) -> list[int]:
    """relevance: position in the matrix -> a score from 0 to 1. The same choice as app.personalisation.mmr."""
    remaining = list(relevance)
    chosen: list[int] = []
    while remaining and len(chosen) < k:
        redundancy = (matrix[remaining] @ matrix[chosen].T).max(axis=1) if chosen else np.zeros(len(remaining))
        scores = [lambda_ * relevance[c] - (1 - lambda_) * float(r) for c, r in zip(remaining, redundancy, strict=True)]
        best = remaining[int(np.argmax(scores))]
        chosen.append(best)
        remaining.remove(best)
    return chosen


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--model", required=True, choices=list(MODELS))
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()

    rng = random.Random(SEED)
    tours = load_tours()
    log = generate_log(tours, rng)
    cutoff = datetime(2025, 1, 1, tzinfo=UTC) + timedelta(days=SPAN_DAYS - SPLIT_DAYS)
    train = [row for row in log if row["when"] < cutoff]
    test = [row for row in log if row["when"] >= cutoff]

    ids, matrix = load_matrix(args.model)
    index = {tour_id: position for position, tour_id in enumerate(ids)}
    counts: dict[int, int] = {}
    histories: dict[int, list[dict]] = {}
    for row in train:
        counts[row["tour"]] = counts.get(row["tour"], 0) + 1
        histories.setdefault(row["user"], []).append(row)
    popular = sorted(ids, key=lambda t: (-counts.get(t, 0), t))
    futures: dict[int, dict[int, int]] = {}
    for row in test:
        futures.setdefault(row["user"], {})[row["tour"]] = 3

    def unseen(ranking, seen):
        return [t for t in ranking if t not in seen]

    systems = {
        name: []
        for name in (
            "popularity",
            "content, last booking",
            "profile",
            "profile, no decay",
            "profile + MMR",
            "profile + popularity (RRF)",
            "profile + popularity + MMR",
            "same, no booking data",
            "same, no booking data, ties by id (the old bug)",
        )
    }
    category = {tour["id"]: tour["category"] for tour in tours}
    # As rank() in app.personalisation: equally popular tours share a rank, so a tie carries no signal.
    first_position: dict[int, int] = {}
    for position, tour_id in enumerate(popular):
        first_position.setdefault(counts.get(tour_id, 0), position + 1)
    popularity_rank = {tour_id: first_position[counts.get(tour_id, 0)] for tour_id in ids}
    no_data = dict.fromkeys(ids, 1)
    by_id = {tour_id: position + 1 for position, tour_id in enumerate(sorted(ids))}

    def fused_mmr(wider: list[int], rank_of: dict[int, int]) -> tuple[list[int], list[int]]:
        """(fused order, the same after MMR over its top 50). The same choice as app.personalisation."""
        score = {t: 1 / (60 + r + 1) + 1 / (60 + rank_of[t]) for r, t in enumerate(wider)}
        fused = sorted(wider, key=lambda t: -score[t])
        best = score[fused[0]]
        top = {index[t]: score[t] / best for t in fused[:50]}
        return fused, [ids[p] for p in mmr_rerank(top, matrix, K)]

    taste_match = {name: [] for name in systems}
    hits = {name: [] for name in systems}
    coverage = {name: set() for name in systems}
    evaluated = 0
    for user, relevant in futures.items():
        history = histories.get(user)
        if not history:
            continue  # a cold start: every system but popularity would be this one anyway
        evaluated += 1
        seen = {row["tour"] for row in history}
        rankings = {"popularity": unseen(popular, seen)[:K]}

        last = matrix[index[history[-1]["tour"]]]
        order = np.argsort(-(matrix @ last))
        rankings["content, last booking"] = unseen((ids[i] for i in order[: K + len(seen) + 1]), seen)[:K]

        vectors = matrix[[index[row["tour"]] for row in history]]
        for name, half_life in (("profile", 180.0), ("profile, no decay", 1e9)):
            profile = (weights(history, cutoff, half_life)[:, None] * vectors).sum(axis=0)
            norm = float(np.linalg.norm(profile))
            if norm == 0:
                rankings[name] = rankings["popularity"]
                continue
            profile = profile / norm
            order = np.argsort(-(matrix @ profile))
            candidates = unseen((ids[i] for i in order[: 50 + len(seen)]), seen)[:50]
            rankings[name] = candidates[:K]
            if name == "profile":
                cosine = {index[t]: float(matrix[index[t]] @ profile) for t in candidates}
                chosen = mmr_rerank(cosine, matrix, K)
                rankings["profile + MMR"] = [ids[p] for p in chosen]

                wider = unseen((ids[i] for i in order[: FUSION_CANDIDATES + len(seen)]), seen)[:FUSION_CANDIDATES]
                fused, diversified = fused_mmr(wider, popularity_rank)
                rankings["profile + popularity (RRF)"] = fused[:K]
                rankings["profile + popularity + MMR"] = diversified
                rankings["same, no booking data"] = fused_mmr(wider, no_data)[1]
                rankings["same, no booking data, ties by id (the old bug)"] = fused_mmr(wider, by_id)[1]
        for name in systems:
            rankings.setdefault(name, rankings["popularity"])

        for name, ranking in rankings.items():
            systems[name].append(ndcg_at(ranking, relevant, K))
            hits[name].append(1.0 if set(ranking) & set(relevant) else 0.0)
            coverage[name].update(ranking)
            taste = set(history[0]["taste"])
            taste_match[name].append(sum(category[t] in taste for t in ranking) / max(len(ranking), 1))

    results = {
        "synthetic": True,
        "users_evaluated": evaluated,
        "interactions": {"train": len(train), "test": len(test)},
        "catalogue": len(ids),
        "systems": {
            name: {
                "hit_rate@10": bootstrap_ci(hits[name]),
                "ndcg@10": bootstrap_ci(values),
                "taste_match@10": bootstrap_ci(taste_match[name]),
                "catalogue_coverage": len(coverage[name]) / len(ids),
            }
            for name, values in systems.items()
        },
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(results, indent=1), encoding="utf-8")
    for name, row in results["systems"].items():
        hit, ndcg, taste = row["hit_rate@10"], row["ndcg@10"], row["taste_match@10"]
        interval = f"{hit[0]:.3f} [{hit[1]:.3f}, {hit[2]:.3f}]"
        print(f"{name:28} taste {taste[0]:.2f} hit@10 {interval} nDCG@10 {ndcg[0]:.3f}", row["catalogue_coverage"])
    print(evaluated, "users evaluated")


if __name__ == "__main__":
    main()
