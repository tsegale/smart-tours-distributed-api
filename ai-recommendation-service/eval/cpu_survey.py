#!/usr/bin/env python3
"""Do an ONNX model's vectors and scores depend on the CPU? Issue #10. Run on many runners by int8-cpu-survey.yml.

    python -m eval.cpu_survey --out survey.json            one machine: every variant, with the reviewed labels
    python -m eval.cpu_survey --report DIR                 many machines: a table, grouped by what the CPU can do

DATABASE_URL points at a migrated, disposable database. Written tours only, as in the CI gate.
"""

import argparse
import hashlib
import json
import statistics
import time
from pathlib import Path

import numpy as np
from sqlalchemy import text

from app.database import SessionLocal
from app.embeddings import MODELS, ModelSpec
from app.gazetteer import load_csv
from app.query_parser import QueryParser
from app.retrieval import Filters, search

from . import queries_known
from .cpu import cpu_features
from .metrics import ndcg_at, recall_at
from .run_eval import curated_queries, embed_corpus, load_read_model, load_tours

REPO = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"
FLOAT = "paraphrase-ml"
VARIANTS = {
    FLOAT: None,
    "paraphrase-ml-int8": None,  # what ships
    "qint8-avx512": ModelSpec(REPO, "onnx/model_qint8_avx512.onnx", max_length=128),
    "quint8-avx2": ModelSpec(REPO, "onnx/model_quint8_avx2.onnx", max_length=128),
}
LATENCY_PASSES = 3


def _plain(embedder, model, query):
    vector = embedder.embed_query(query)
    with SessionLocal() as db, db.begin():
        hits = search(db, query_text=query, query_vector=vector, model=model, mode="hybrid", limit=20)
    return [hit.tour_id for hit in hits]


def _chosen(embedder, model, parser, query):
    """What the endpoint does: phrases become filters, and the rest ranks."""
    understood = parser.parse(query)
    vector = embedder.embed_query(understood.text)
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
            query_vector=vector,
            model=model,
            filters=filters,
            mode="hybrid",
            limit=20,
            lexical_weight=0.5,
        )
    return [hit.tour_id for hit in hits]


def _scores(system, queries) -> dict:
    rankings = [system(query["q"]) for query in queries]
    pairs = list(zip(rankings, (query["judgments"] for query in queries), strict=True))
    return {
        "ndcg@10": sum(ndcg_at(r, j, 10) for r, j in pairs) / len(pairs),
        "recall@20": sum(recall_at(r, j, 20) for r, j in pairs) / len(pairs),
    }


def measure() -> dict:
    tours = [tour for tour in load_tours() if tour["authored"]]
    load_read_model(tours)
    with SessionLocal() as db, db.begin():
        db.execute(text("DELETE FROM tour_embeddings"))
    curated = curated_queries(tours)
    known = queries_known.build(tours)
    parser = QueryParser(load_csv())
    texts = [query["q"] for query in curated]

    result: dict = {"cpu": cpu_features(), "variants": {}}
    reference = None
    for name, spec in VARIANTS.items():
        if spec is not None:
            MODELS[name] = spec
        embedder = embed_corpus(tours, name)["embedder"]

        vectors = np.array([embedder.embed_query(q) for q in texts], dtype=np.float64)
        if name == FLOAT:
            reference = vectors
        cosines = (vectors * reference).sum(axis=1)

        timings = []
        for _ in range(LATENCY_PASSES):
            for query in texts:
                began = time.perf_counter()
                embedder.embed_query(query)
                timings.append((time.perf_counter() - began) * 1000)

        result["variants"][name] = {
            "vector_sha": hashlib.sha256(np.round(vectors, 5).tobytes()).hexdigest()[:12],
            "cosine_to_float_mean": float(cosines.mean()),
            "cosine_to_float_min": float(cosines.min()),
            "query_ms_p50": statistics.median(timings),
            "gate": _scores(lambda q, e=embedder, n=name: _plain(e, n, q), curated),
            "chosen": _scores(lambda q, e=embedder, n=name: _chosen(e, n, parser, q), curated),
            "chosen_known": _scores(lambda q, e=embedder, n=name: _chosen(e, n, parser, q), known),
        }
        print(name, json.dumps(result["variants"][name]), flush=True)
    return result


def report(directory: Path) -> str:
    runs = [json.loads(path.read_text(encoding="utf-8")) for path in sorted(directory.rglob("*.json"))]
    groups: dict[tuple, list[dict]] = {}
    for run in runs:
        cpu = run["cpu"]
        groups.setdefault((cpu["vnni"], cpu["avx512"], cpu["avx2"], cpu["model"]), []).append(run)
    lines = [
        f"{len(runs)} jobs",
        "",
        "| VNNI | AVX-512 | AVX2 | CPU | Jobs | Variant | Gate nDCG@10 | Gate recall@20 | Chosen nDCG@10 "
        "| Known nDCG@10 | Cosine to float, mean / min | Query p50 ms | Distinct vectors |",
        "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |",
    ]

    def spread(values: list[float]) -> str:
        low, high = min(values), max(values)
        return f"{low:.4f}" if high - low < 0.00005 else f"{low:.4f} to {high:.4f}"

    for (vnni, avx512, avx2, model), members in sorted(groups.items(), key=lambda item: str(item[0])):
        for name in members[0]["variants"]:
            rows = [run["variants"][name] for run in members]
            lines.append(
                f"| {vnni} | {avx512} | {avx2} | {model} | {len(members)} | {name} "
                f"| {spread([r['gate']['ndcg@10'] for r in rows])} | {spread([r['gate']['recall@20'] for r in rows])} "
                f"| {spread([r['chosen']['ndcg@10'] for r in rows])} "
                f"| {spread([r['chosen_known']['ndcg@10'] for r in rows])} "
                f"| {statistics.mean(r['cosine_to_float_mean'] for r in rows):.5f} / "
                f"{min(r['cosine_to_float_min'] for r in rows):.5f} "
                f"| {statistics.median(r['query_ms_p50'] for r in rows):.1f} "
                f"| {len({r['vector_sha'] for r in rows})} |"
            )
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--out", type=Path)
    parser.add_argument("--report", type=Path)
    args = parser.parse_args()
    if args.report:
        print(report(args.report))
        return
    args.out.write_text(json.dumps(measure(), indent=1), encoding="utf-8")


if __name__ == "__main__":
    main()
