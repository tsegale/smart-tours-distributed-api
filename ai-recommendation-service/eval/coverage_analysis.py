#!/usr/bin/env python3
"""Why does dense lead the chosen hybrid on the coverage set, and would anything fix it without breaking the rest?

    python -m eval.coverage_analysis --out eval/results/coverage_analysis.json

Run from ai-recommendation-service, with DATABASE_URL pointing at the database eval.run_eval filled. Candidates here are
experiments, and none of them is what the service runs. They are scored on every query set, because a change aimed at
conversational sentences can quietly cost exact names.
"""

import argparse
import json
import re
from collections.abc import Callable
from pathlib import Path

from sqlalchemy import text

from app.database import SessionLocal
from app.embeddings import MODELS, OnnxEmbedder
from app.gazetteer import load_csv
from app.query_parser import QueryParser
from app.retrieval import Filters, Hit, search

from .metrics import bootstrap_ci, ndcg_at, paired_difference_ci
from .run_eval import load_tours, query_sets

MODEL = "paraphrase-ml-uint8"
CHOSEN = "chosen"
SETS = ("curated", "coverage", "rule", "known", "titles", "long titles")
LONG_TITLE = 6

# Everything from the negating word on. Deliberately crude: this tests whether the idea is worth having.
NEGATION = re.compile(
    r"[,\s]*\b(not|without|instead of|rather than|ohne|statt|anstatt|nicht|kein\w*)\b.*$", re.IGNORECASE
)


def strip_negation(query: str) -> str:
    stripped = NEGATION.sub("", query).strip()
    return stripped or query


class Runner:
    def __init__(self) -> None:
        tours = load_tours()
        self.sets = query_sets(tours)
        written = tuple(t["id"] for t in tours if t["authored"])
        composed = tuple(t["id"] for t in tours if not t["authored"])
        self.corpora = {"curated": written, "coverage": written, "known": written, "rule": composed}
        # A whole tour name pasted in: the case a length-based rule could break, and the known-item set doesn't have.
        titles = [
            {"q": t["name"], "language": t["language"], "judgments": {t["id"]: 3}} for t in tours if t["authored"]
        ]
        self.sets["titles"] = titles
        self.sets["long titles"] = [query for query in titles if tokens(query["q"]) >= LONG_TITLE]
        self.corpora["titles"] = self.corpora["long titles"] = written
        self.names = {t["id"]: t["name"] for t in tours}
        self.parser = QueryParser(load_csv())
        self.embedder = OnnxEmbedder(MODEL, MODELS[MODEL])
        self.embedder.warm_up()

    def hits(
        self,
        query: str,
        corpus: tuple[int, ...],
        *,
        mode: str = "hybrid",
        weight: float | Callable[[str], float] = 0.5,
        parse: bool = True,
        lexical_text: Callable[[str], str] = str,
        dense_text: Callable[[str], str] = str,
    ) -> list[Hit]:
        understood = self.parser.parse(query) if parse else None
        remainder = understood.text if understood else query
        filters = Filters(
            latitude=understood.latitude if understood else None,
            longitude=understood.longitude if understood else None,
            radius_km=understood.radius_km if understood else None,
            max_price=understood.max_price if understood else None,
            max_minutes=understood.max_minutes if understood else None,
            min_minutes=understood.min_minutes if understood else None,
            only_ids=corpus,
        )
        vector = None if mode == "lexical" else self.embedder.embed_query(dense_text(remainder))
        with SessionLocal() as db, db.begin():
            return search(
                db,
                query_text=lexical_text(remainder),
                query_vector=vector,
                model=MODEL,
                filters=filters,
                mode=mode,
                limit=20,
                lexical_weight=weight(remainder) if callable(weight) else weight,
            )

    def ndcg(self, set_name: str, **options) -> list[float]:
        scores = []
        for query in self.sets[set_name]:
            hits = self.hits(query["q"], self.corpora[set_name], **options)
            scores.append(ndcg_at([h.tour_id for h in hits], query["judgments"], 10))
        return scores


def tokens(query: str) -> int:
    return len(re.findall(r"\w+", query))


def by_length(long_from: int, short: float = 0.5, long: float = 0.1) -> Callable[[str], float]:
    return lambda query: long if tokens(query) >= long_from else short


CANDIDATES: dict[str, dict] = {
    CHOSEN: {},
    "dense + parser (no lexical list)": {"mode": "dense"},
    "dense, no parser": {"mode": "dense", "parse": False},
    "weight 0.1": {"weight": 0.1},
    "weight 0.25": {"weight": 0.25},
    "weight 0.75": {"weight": 0.75},
    "weight 1.0": {"weight": 1.0},
    "0.1 from 6 words": {"weight": by_length(6)},
    "0.1 from 8 words": {"weight": by_length(8)},
    "0.25 from 8 words": {"weight": by_length(8, long=0.25)},
    "negation stripped, lexical only": {"lexical_text": strip_negation},
    "negation stripped, both lists": {"lexical_text": strip_negation, "dense_text": strip_negation},
    "negation stripped + 0.1 from 8 words": {"lexical_text": strip_negation, "weight": by_length(8)},
}


def spearman(xs: list[float], ys: list[float]) -> float:
    def ranks(values: list[float]) -> list[float]:
        order = sorted(range(len(values)), key=values.__getitem__)
        result = [0.0] * len(values)
        position = 0
        while position < len(order):
            end = position
            while end + 1 < len(order) and values[order[end + 1]] == values[order[position]]:
                end += 1
            for index in order[position : end + 1]:
                result[index] = (position + end) / 2
            position = end + 1
        return result

    rx, ry = ranks(xs), ranks(ys)
    mean_x, mean_y = sum(rx) / len(rx), sum(ry) / len(ry)
    top = sum((a - mean_x) * (b - mean_y) for a, b in zip(rx, ry, strict=True))
    bottom = (sum((a - mean_x) ** 2 for a in rx) * sum((b - mean_y) ** 2 for b in ry)) ** 0.5
    return top / bottom if bottom else 0.0


def matched_words(tour_id: int, query: str) -> list[str]:
    statement = text(
        """
        SELECT word FROM (
            SELECT unnest(tsvector_to_array(search_vector)) AS word FROM ai_tours WHERE tour_id = :id
            INTERSECT
            SELECT unnest(tsvector_to_array(to_tsvector('english', :q) || to_tsvector('german', :q)))
        ) shared ORDER BY word
        """
    )
    with SessionLocal() as db:
        return list(db.execute(statement, {"id": tour_id, "q": query}).scalars())


def per_query(runner: Runner, set_name: str) -> list[dict]:
    """For each query: what the lexical list pushed into the top 10, and what that cost."""
    rows = []
    for query in runner.sets[set_name]:
        corpus = runner.corpora[set_name]
        judgments = query["judgments"]
        chosen = runner.hits(query["q"], corpus)
        dense = runner.hits(query["q"], corpus, mode="dense")
        lexical = runner.hits(query["q"], corpus, mode="lexical")
        chosen_top = [h.tour_id for h in chosen[:10]]
        dense_top = [h.tour_id for h in dense[:10]]
        promoted = [t for t in chosen_top if t not in dense_top]
        displaced = [t for t in dense_top if t not in chosen_top]
        rows.append(
            {
                "q": query["q"],
                "language": query["language"],
                "tokens": tokens(query["q"]),
                "negated": strip_negation(query["q"]) != query["q"],
                "chosen": ndcg_at([h.tour_id for h in chosen], judgments, 10),
                "dense": ndcg_at([h.tour_id for h in dense], judgments, 10),
                "lexical": ndcg_at([h.tour_id for h in lexical], judgments, 10),
                "promoted": [
                    {
                        "name": runner.names[t],
                        "grade": judgments.get(t, 0),
                        "matched": matched_words(t, query["q"]),
                    }
                    for t in promoted
                ],
                "displaced": [{"name": runner.names[t], "grade": judgments.get(t, 0)} for t in displaced],
            }
        )
    return rows


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    runner = Runner()

    scores: dict[str, dict[str, list[float]]] = {}
    for name, options in CANDIDATES.items():
        scores[name] = {set_name: runner.ndcg(set_name, **options) for set_name in SETS}
        scores[name]["curated+coverage"] = scores[name]["curated"] + scores[name]["coverage"]
        print(name, {k: round(sum(v) / len(v), 3) for k, v in scores[name].items()}, flush=True)

    summary: dict = {}
    for name, by_set in scores.items():
        summary[name] = {}
        for set_name, values in by_set.items():
            cell: dict = {"ndcg": bootstrap_ci(values)}
            if name != CHOSEN:
                cell["paired"] = list(paired_difference_ci(values, scores[CHOSEN][set_name]))
            summary[name][set_name] = cell

    detail = {set_name: per_query(runner, set_name) for set_name in ("coverage", "curated")}
    both = detail["coverage"] + detail["curated"]
    gains = [row["dense"] - row["chosen"] for row in both]

    def group(rows: list[dict]) -> dict:
        n = len(rows)
        return {
            "queries": n,
            "dense_minus_chosen": sum(r["dense"] - r["chosen"] for r in rows) / n if n else None,
            "chosen": sum(r["chosen"] for r in rows) / n if n else None,
            "dense": sum(r["dense"] for r in rows) / n if n else None,
            "lexical": sum(r["lexical"] for r in rows) / n if n else None,
            "tokens": sum(r["tokens"] for r in rows) / n if n else None,
            "promoted_mean_grade": _mean(p["grade"] for r in rows for p in r["promoted"]),
            "displaced_mean_grade": _mean(p["grade"] for r in rows for p in r["displaced"]),
            "promoted": sum(len(r["promoted"]) for r in rows),
        }

    results = {
        "model": MODEL,
        "summary": summary,
        "detail": detail,
        "groups": {
            "coverage": group(detail["coverage"]),
            "curated": group(detail["curated"]),
            "negated": group([r for r in both if r["negated"]]),
            "negated, coverage": group([r for r in detail["coverage"] if r["negated"]]),
            "negated, curated": group([r for r in detail["curated"] if r["negated"]]),
            "not negated, coverage": group([r for r in detail["coverage"] if not r["negated"]]),
            "under 6 words": group([r for r in both if r["tokens"] < 6]),
            "6 to 8 words": group([r for r in both if 6 <= r["tokens"] <= 8]),
            "9 words or more": group([r for r in both if r["tokens"] > 8]),
        },
        "length_vs_gain_spearman": spearman([float(r["tokens"]) for r in both], gains),
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(results, indent=1, ensure_ascii=False) + "\n", encoding="utf-8", newline="\n")
    print("wrote", args.out)


def _mean(values) -> float | None:
    values = list(values)
    return sum(values) / len(values) if values else None


if __name__ == "__main__":
    main()
