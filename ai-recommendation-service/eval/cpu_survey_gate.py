#!/usr/bin/env python3
"""Two follow-ups to the CPU survey (issue #10), run on many runners by gate-cpu-survey.yml.

Does the gate's coverage line hold the tolerance across CPUs? Do the cross-encoders depend on the CPU like the int8
embedding file did, and does their U8U8 build fix it?

    python -m eval.cpu_survey_gate --out survey.json            one machine
    python -m eval.cpu_survey_gate --report DIR --json FILE     many machines: VNNI against no VNNI

DATABASE_URL points at a migrated, disposable database. Written tours only, as in the CI gate.
"""

import argparse
import json
import statistics
import time
from pathlib import Path

from app.database import SessionLocal
from app.embeddings import MODELS, OnnxEmbedder, document_text
from app.gazetteer import load_csv
from app.query_parser import QueryParser
from app.reranker import RERANKERS, OnnxCrossEncoder
from app.retrieval import Filters, hydrate, search

from .cpu import cpu_features
from .curated_coverage import CURATED_COVERAGE
from .metrics import ndcg_at, recall_at
from .regression import measure as gate_lines
from .run_eval import curated_queries, load_tours

SHIPPED = "paraphrase-ml-uint8"
OLD = "paraphrase-ml-int8"
DEPTH = 50


def _candidates(embedder, parser, query: str) -> tuple[str, list[int], list[str]]:
    understood = parser.parse(query)
    filters = Filters(
        latitude=understood.latitude,
        longitude=understood.longitude,
        radius_km=understood.radius_km,
        max_price=understood.max_price,
        max_minutes=understood.max_minutes,
        min_minutes=understood.min_minutes,
    )
    with SessionLocal() as db, db.begin():
        hits = search(
            db,
            query_text=understood.text,
            query_vector=embedder.embed_query(understood.text),
            model=SHIPPED,
            filters=filters,
            mode="hybrid",
            limit=DEPTH,
            lexical_weight=0.5,
        )
        tours = hydrate(db, [hit.tour_id for hit in hits])
    ids = [hit.tour_id for hit in hits]
    return understood.text, ids, [document_text(tours[i]["name"], tours[i]["description"]) for i in ids]


def measure() -> dict:
    result: dict = {"cpu": cpu_features(), "gate": {model: gate_lines(model) for model in (OLD, SHIPPED)}}

    # gate_lines left the shipped model's vectors in the database, over the written tours.
    tours = [tour for tour in load_tours() if tour["authored"]]
    embedder = OnnxEmbedder(SHIPPED, MODELS[SHIPPED])
    parser = QueryParser(load_csv())
    queries = curated_queries(tours) + curated_queries(tours, CURATED_COVERAGE, "coverage")
    candidates = [_candidates(embedder, parser, query["q"]) for query in queries]

    result["rerankers"] = {}
    for name, spec in RERANKERS.items():
        cross_encoder = OnnxCrossEncoder(name, spec)
        ndcg, recall, scored, timings = [], [], {}, []
        for query, (understood, ids, documents) in zip(queries, candidates, strict=True):
            began = time.perf_counter()
            scores = cross_encoder.score(understood, documents)
            timings.append((time.perf_counter() - began) * 1000)
            ranking = [tour for tour, _ in sorted(zip(ids, scores, strict=True), key=lambda pair: -pair[1])][:20]
            ndcg.append(ndcg_at(ranking, query["judgments"], 10))
            recall.append(recall_at(ranking, query["judgments"], 20))
            scored[query["id"]] = {str(i): round(s, 4) for i, s in zip(ids, scores, strict=True)}
        result["rerankers"][name] = {
            "ndcg@10": sum(ndcg) / len(ndcg),
            "recall@20": sum(recall) / len(recall),
            "ms_p50": statistics.median(timings),
            "scores": scored,
        }
        print(name, round(result["rerankers"][name]["ndcg@10"], 4), flush=True)
    return result


def _distinct(values) -> list[float]:
    return sorted({round(value, 4) for value in values})


def _spread(values: list[float]) -> float:
    return round(max(values) - min(values), 4)


def summarise(directory: Path) -> dict:
    runs = [json.loads(path.read_text(encoding="utf-8")) for path in sorted(directory.rglob("*.json"))]
    sides = {"vnni": [r for r in runs if r["cpu"]["vnni"]], "no_vnni": [r for r in runs if not r["cpu"]["vnni"]]}
    summary: dict = {
        "jobs": {side: len(members) for side, members in sides.items()},
        "cpus": sorted({f"{r['cpu']['model']}, VNNI {r['cpu']['vnni']}" for r in runs}),
        "gate": {},
        "rerankers": {},
    }
    for model in (OLD, SHIPPED):
        lines = {}
        for label, read in {
            "curated hybrid ndcg@10": lambda g: g["hybrid"]["ndcg@10"],
            "curated hybrid recall@20": lambda g: g["hybrid"]["recall@20"],
            "curated lexical ndcg@10": lambda g: g["lexical"]["ndcg@10"],
            "curated lexical recall@20": lambda g: g["lexical"]["recall@20"],
            "coverage hybrid ndcg@10": lambda g: g["coverage"]["hybrid"]["ndcg@10"],
            "coverage hybrid recall@20": lambda g: g["coverage"]["hybrid"]["recall@20"],
            "coverage lexical ndcg@10": lambda g: g["coverage"]["lexical"]["ndcg@10"],
            "coverage lexical recall@20": lambda g: g["coverage"]["lexical"]["recall@20"],
        }.items():
            row = {side: _distinct(read(r["gate"][model]) for r in members) for side, members in sides.items()}
            row["spread"] = _spread(row["vnni"] + row["no_vnni"])
            lines[label] = row
        summary["gate"][model] = lines
    for name in RERANKERS:
        row: dict = {}
        for side, members in sides.items():
            cells = [r["rerankers"][name] for r in members]
            row[side] = {
                "ndcg@10": _distinct(c["ndcg@10"] for c in cells),
                "recall@20": _distinct(c["recall@20"] for c in cells),
                "ms_p50": statistics.median(c["ms_p50"] for c in cells),
                "distinct_scores": len({json.dumps(c["scores"], sort_keys=True) for c in cells}),
            }
        one, other = sides["vnni"][0]["rerankers"][name]["scores"], sides["no_vnni"][0]["rerankers"][name]["scores"]
        differences = [
            abs(score - other[query][tour])
            for query, by_tour in one.items()
            for tour, score in by_tour.items()
            if tour in other[query]
        ]
        row["score_difference_mean"] = statistics.mean(differences)
        row["score_difference_max"] = max(differences)
        row["ndcg_spread"] = _spread(row["vnni"]["ndcg@10"] + row["no_vnni"]["ndcg@10"])
        summary["rerankers"][name] = row
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--out", type=Path)
    parser.add_argument("--report", type=Path)
    parser.add_argument("--json", type=Path)
    args = parser.parse_args()
    if args.report:
        summary = summarise(args.report)
        if args.json:
            args.json.write_text(json.dumps(summary, indent=1) + chr(10), encoding="utf-8", newline=chr(10))
        print(json.dumps(summary, indent=1))
        return
    args.out.write_text(json.dumps(measure()), encoding="utf-8")


if __name__ == "__main__":
    main()
