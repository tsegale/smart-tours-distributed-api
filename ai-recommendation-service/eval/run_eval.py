#!/usr/bin/env python3
"""Offline retrieval evaluation. Results decide modelling choices: docs/benchmarks/search-quality.md.

Run from ai-recommendation-service, with DATABASE_URL pointing at a migrated database:

    python -m eval.run_eval --models minilm-l6 e5-small --out eval/results/run.json
"""

from __future__ import annotations

import argparse
import json
import random
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

from sqlalchemy import text

from app import read_model, repository
from app.database import SessionLocal
from app.embeddings import MODELS, OnnxEmbedder, document_text
from app.gazetteer import load_csv
from app.query_parser import QueryParser
from app.reranker import RERANKERS, OnnxCrossEncoder
from app.retrieval import Filters, hydrate, search

from . import queries_known, queries_rule
from .curated import CURATED
from .curated_coverage import CURATED_COVERAGE
from .metrics import bootstrap_ci, ndcg_at, percentile, recall_at, reciprocal_rank

DATASET = Path(__file__).resolve().parents[2] / "datasets" / "tours_v1" / "tours.jsonl"
BATCH = 32


def load_tours() -> list[dict]:
    with DATASET.open(encoding="utf-8") as lines:
        return [json.loads(line) for line in lines]


def load_read_model(tours: list[dict]) -> None:
    """Tours and one departure each, directly: this evaluates ranking, not the events that normally fill the table."""
    rng = random.Random(11)
    with SessionLocal() as db, db.begin():
        db.execute(text("TRUNCATE ai_tours, ai_departures"))
        for tour in tours:
            read_model.upsert_tour(
                db,
                tour_id=tour["id"],
                name=tour["name"],
                description=tour["description"],
                language=tour["language"],
                latitude=tour["latitude"],
                longitude=tour["longitude"],
                price=tour["price"],
                currency=tour["currency"],
                category=tour["category"],
                difficulty=tour["difficulty"],
                duration_minutes=tour["durationMinutes"],
                version=1,
            )
            read_model.upsert_departure(
                db,
                availability_id=tour["id"],
                tour_id=tour["id"],
                starts_at=datetime.now(UTC) + timedelta(days=rng.randint(1, 180)),
                total_slots=12,
                slots_left=rng.randint(0, 12),
                version=1,
            )
        read_model.analyze(db)


def embed_corpus(tours: list[dict], name: str) -> dict:
    embedder = OnnxEmbedder(name, MODELS[name])
    began = time.perf_counter()
    embedder.warm_up()
    load_s = time.perf_counter() - began
    with SessionLocal() as db:
        have = db.execute(text("SELECT count(*) FROM tour_embeddings WHERE model = :m"), {"m": name}).scalar_one()
    embed_s = 0.0
    if have != len(tours):
        with SessionLocal() as db, db.begin():
            db.execute(text("DELETE FROM tour_embeddings WHERE model = :m"), {"m": name})
        began = time.perf_counter()
        for start in range(0, len(tours), BATCH):
            chunk = tours[start : start + BATCH]
            contents = [document_text(t["name"], t["description"]) for t in chunk]
            vectors = embedder.embed_documents(contents)
            with SessionLocal() as db, db.begin():
                for tour, content, vector in zip(chunk, contents, vectors, strict=True):
                    repository.upsert_embedding(
                        db,
                        tour_id=tour["id"],
                        title=tour["name"],
                        content=content,
                        embedding=vector,
                        model=name,
                        event_id=None,
                    )
        embed_s = time.perf_counter() - began
    return {"embedder": embedder, "load_s": load_s, "embed_s": embed_s}


def curated_queries(tours: list[dict], source: list[tuple] = CURATED, prefix: str = "curated") -> list[dict]:
    by_name: dict[str, list[int]] = {}
    for tour in tours:
        if tour["authored"]:
            by_name.setdefault(tour["name"], []).append(tour["id"])
    curated = []
    for index, (language, shape, query, named) in enumerate(source):
        judgments = {}
        for name, grade in named.items():
            if name not in by_name:
                raise SystemExit(f"curated query {query!r} names an unknown tour: {name!r}")
            judgments.update(dict.fromkeys(by_name[name], grade))
        curated.append(
            {"id": f"{prefix}-{index}", "q": query, "language": language, "shape": shape, "judgments": judgments}
        )
    return curated


def query_sets(tours: list[dict]) -> dict[str, list[dict]]:
    curated = curated_queries(tours)
    written = [tour for tour in tours if tour["authored"]]
    return {
        "curated": curated,
        "rule": queries_rule.build(tours),
        "known": queries_known.build(written),
        # Not reviewed, and reported apart: nothing is decided on it.
        "coverage": curated_queries(tours, CURATED_COVERAGE, "coverage"),
    }


def reranked(embedder, name, cross_encoder, depth, parser, weight):
    """What the endpoint does with a reranker on: parse, fuse, re-order. The latency recorded includes all of it."""

    def rerank_system(query, filters):
        understood = parser.parse(query)
        vector = embedder.embed_query(understood.text)
        narrowed = Filters(
            latitude=understood.latitude,
            longitude=understood.longitude,
            radius_km=understood.radius_km,
            max_price=understood.max_price,
            max_minutes=understood.max_minutes,
            min_minutes=understood.min_minutes,
            only_ids=filters.only_ids,
        )
        with SessionLocal() as db, db.begin():
            hits = search(
                db,
                query_text=understood.text,
                query_vector=vector,
                model=name,
                filters=narrowed,
                mode="hybrid",
                limit=depth,
                lexical_weight=weight,
            )
            tours = hydrate(db, [h.tour_id for h in hits])
        documents = [document_text(tours[h.tour_id]["name"], tours[h.tour_id]["description"]) for h in hits]
        scores = cross_encoder.score(understood.text, documents)
        ordered = sorted(zip(hits, scores, strict=True), key=lambda pair: -pair[1])
        return [hit.tour_id for hit, _ in ordered][:20]

    return rerank_system


def run(system, queries, only_ids=None) -> dict:
    """system(query, filters) -> ranked ids. Per-query metrics, so that bootstrapping can resample queries."""
    per_query = {"ndcg": [], "recall": [], "mrr": [], "ms": [], "language": [q["language"] for q in queries]}
    for query in queries:
        began = time.perf_counter()
        ranking = system(query["q"], Filters(only_ids=only_ids))
        per_query["ms"].append((time.perf_counter() - began) * 1000)
        per_query["ndcg"].append(ndcg_at(ranking, query["judgments"], 10))
        per_query["recall"].append(recall_at(ranking, query["judgments"], 20))
        per_query["mrr"].append(reciprocal_rank(ranking, query["judgments"]))
    return per_query


def summarise(per_query: dict) -> dict:
    result = {}
    for metric in ("ndcg", "recall", "mrr"):
        result[metric] = bootstrap_ci(per_query[metric])
    result["ms_p50"] = percentile(per_query["ms"], 0.50)
    result["ms_p95"] = percentile(per_query["ms"], 0.95)
    for language in ("en", "de"):
        values = [v for v, lang in zip(per_query["ndcg"], per_query["language"], strict=True) if lang == language]
        if values:
            result[f"ndcg_{language}"] = bootstrap_ci(values)
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--models", nargs="+", default=["minilm-l6"], choices=list(MODELS))
    parser.add_argument("--rrf-k", nargs="+", type=int, default=[60])
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--skip-load", action="store_true")
    parser.add_argument("--lexical-weights", nargs="*", type=float, default=[])
    parser.add_argument("--rerankers", nargs="*", default=[], choices=list(RERANKERS))
    parser.add_argument("--rerank-depth", type=int, default=50)
    args = parser.parse_args()

    tours = load_tours()
    if not args.skip_load:
        load_read_model(tours)
    sets = query_sets(tours)
    written_ids = tuple(t["id"] for t in tours if t["authored"])
    composed_ids = tuple(t["id"] for t in tours if not t["authored"])
    corpora = {"curated": written_ids, "rule": composed_ids, "known": written_ids, "coverage": written_ids}
    print({name: len(queries) for name, queries in sets.items()}, flush=True)

    parser = QueryParser(load_csv())
    results: dict = {"queries": {k: len(v) for k, v in sets.items()}, "systems": {}, "per_query": {}, "models": {}}

    def record(label, set_name, per_query):
        results["systems"].setdefault(label, {})[set_name] = summarise(per_query)
        results["per_query"].setdefault(label, {})[set_name] = per_query
        summary = results["systems"][label][set_name]
        ndcg = summary["ndcg"]
        interval = f"{ndcg[0]:.3f} [{ndcg[1]:.3f}, {ndcg[2]:.3f}]"
        print(f"{label:34} {set_name:8} nDCG@10 {interval} p50 {summary['ms_p50']:.0f} ms", flush=True)

    cross_encoders = {n: OnnxCrossEncoder(n, RERANKERS[n]) for n in args.rerankers}

    def lexical(query, filters):
        with SessionLocal() as db, db.begin():
            hits = search(db, query_text=query, query_vector=None, model="", filters=filters, mode="lexical", limit=20)
        return [h.tour_id for h in hits]

    for set_name, queries in sets.items():
        record("lexical", set_name, run(lexical, queries, corpora[set_name]))

    for name in args.models:
        info = embed_corpus(tours, name)
        embedder = info["embedder"]
        results["models"][name] = {"load_s": info["load_s"], "embed_s": info["embed_s"], "docs": len(tours)}
        began = time.perf_counter()
        for _ in range(20):
            embedder.embed_query("a short test query about the desert")
        results["models"][name]["query_embed_ms"] = (time.perf_counter() - began) / 20 * 1000

        def dense(query, filters, embedder=embedder, name=name):
            vector = embedder.embed_query(query)
            with SessionLocal() as db, db.begin():
                hits = search(
                    db, query_text="", query_vector=vector, model=name, filters=filters, mode="dense", limit=20
                )
            return [h.tour_id for h in hits]

        for set_name, queries in sets.items():
            record(f"dense:{name}", set_name, run(dense, queries, corpora[set_name]))
            for k in args.rrf_k:

                def hybrid(query, filters, embedder=embedder, name=name, k=k):
                    vector = embedder.embed_query(query)
                    with SessionLocal() as db, db.begin():
                        hits = search(db, query_text=query, query_vector=vector, model=name, filters=filters,
                                      mode="hybrid", limit=20, rrf_k=k, lexical_weight=1.0)  # fmt: skip
                    return [h.tour_id for h in hits]

                record(f"hybrid:{name}:k{k}", set_name, run(hybrid, queries, corpora[set_name]))

            for weight in args.lexical_weights:

                def weighted(query, filters, embedder=embedder, name=name, weight=weight):
                    vector = embedder.embed_query(query)
                    with SessionLocal() as db, db.begin():
                        hits = search(
                            db,
                            query_text=query,
                            query_vector=vector,
                            model=name,
                            filters=filters,
                            mode="hybrid",
                            limit=20,
                            lexical_weight=weight,
                        )
                    return [h.tour_id for h in hits]

                record(f"hybrid:{name}:w{weight}", set_name, run(weighted, queries, corpora[set_name]))

            # What the endpoint does: place, price and duration phrases become filters, the rest ranks.
            def parsed(mode, weight, embedder=embedder, name=name):
                def system(query, filters):
                    understood = parser.parse(query)
                    vector = embedder.embed_query(understood.text)
                    narrowed = Filters(
                        latitude=understood.latitude,
                        longitude=understood.longitude,
                        radius_km=understood.radius_km,
                        max_price=understood.max_price,
                        max_minutes=understood.max_minutes,
                        min_minutes=understood.min_minutes,
                        only_ids=filters.only_ids,
                    )
                    with SessionLocal() as db, db.begin():
                        hits = search(
                            db,
                            query_text=understood.text,
                            query_vector=vector,
                            model=name,
                            filters=narrowed,
                            mode=mode,
                            limit=20,
                            lexical_weight=weight,
                        )
                    return [h.tour_id for h in hits]

                return system

            record(f"hybrid+parser:{name}", set_name, run(parsed("hybrid", 1.0), queries, corpora[set_name]))
            record(f"dense+parser:{name}", set_name, run(parsed("dense", 1.0), queries, corpora[set_name]))
            for weight in args.lexical_weights:
                label = f"hybrid+parser:{name}:w{weight}"
                record(label, set_name, run(parsed("hybrid", weight), queries, corpora[set_name]))

            for reranker_name in args.rerankers:
                weight = args.lexical_weights[0] if args.lexical_weights else 1.0
                rerank_system = reranked(
                    embedder, name, cross_encoders[reranker_name], args.rerank_depth, parser, weight
                )
                label = f"hybrid+parser+rerank:{name}:{reranker_name}"
                record(label, set_name, run(rerank_system, queries, corpora[set_name]))

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(results, indent=1))
    print("wrote", args.out)


if __name__ == "__main__":
    main()
