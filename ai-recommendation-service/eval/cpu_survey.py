#!/usr/bin/env python3
"""Do an ONNX model's vectors and scores depend on the CPU? Issue #10. Run on many runners by int8-cpu-survey.yml.

    python -m eval.cpu_survey --out survey.json --models DIR    one machine: every variant, with the reviewed labels
    python -m eval.cpu_survey --report DIR --json FILE          many machines: VNNI against no VNNI

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
from .curated_coverage import CURATED_COVERAGE
from .metrics import ndcg_at, paired_difference_ci, recall_at
from .run_eval import curated_queries, embed_corpus, load_read_model, load_tours

REPO = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"
FLOAT = "paraphrase-ml"
SHIPPED = "paraphrase-ml-int8"
LOCAL = "local:"
VARIANTS = {
    FLOAT: None,
    SHIPPED: None,
    "upstream-quint8-avx2": ModelSpec(REPO, "onnx/model_quint8_avx2.onnx", max_length=128),
    # Built from the float model by eval/quantize_candidates.py.
    "qint8-rr": ModelSpec(REPO, LOCAL + "qint8-rr.onnx", max_length=128),
    "qint8-rr-pc": ModelSpec(REPO, LOCAL + "qint8-rr-pc.onnx", max_length=128),
    "quint8": ModelSpec(REPO, LOCAL + "quint8.onnx", max_length=128),
    "quint8-pc": ModelSpec(REPO, LOCAL + "quint8-pc.onnx", max_length=128),
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
    per_query = [ndcg_at(r, j, 10) for r, j in pairs]
    return {
        "ndcg@10": sum(per_query) / len(pairs),
        "recall@20": sum(recall_at(r, j, 20) for r, j in pairs) / len(pairs),
        "per_query": per_query,
    }


def _use_local_models(directory: Path) -> None:
    """Candidates aren't published anywhere, so they load from a folder. Everything else still comes from the hub."""
    import huggingface_hub

    original = huggingface_hub.hf_hub_download

    def download(repo, filename, **options):
        if filename.startswith(LOCAL):
            return str(directory / filename.removeprefix(LOCAL))
        return original(repo, filename, **options)

    huggingface_hub.hf_hub_download = download


def measure() -> dict:
    tours = [tour for tour in load_tours() if tour["authored"]]
    load_read_model(tours)
    with SessionLocal() as db, db.begin():
        db.execute(text("DELETE FROM tour_embeddings"))
    curated = curated_queries(tours)
    coverage = curated_queries(tours, CURATED_COVERAGE, "coverage")
    known = queries_known.build(tours)
    parser = QueryParser(load_csv())
    texts = [query["q"] for query in curated + coverage]

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

        def chosen(q, e=embedder, n=name):
            return _chosen(e, n, parser, q)

        def plain(q, e=embedder, n=name):
            return _plain(e, n, q)

        result["variants"][name] = {
            "vectors": np.round(vectors, 6).tolist(),
            "vector_sha": hashlib.sha256(np.round(vectors, 4).tobytes()).hexdigest()[:12],
            "cosine_to_float_mean": float(cosines.mean()),
            "cosine_to_float_min": float(cosines.min()),
            "query_ms_p50": statistics.median(timings),
            "gate": _scores(plain, curated),
            "chosen": _scores(chosen, curated),
            "chosen_coverage": _scores(chosen, coverage),
            "chosen_known": _scores(chosen, known),
        }
        row = result["variants"][name]
        print(name, row["vector_sha"], round(row["gate"]["ndcg@10"], 4), round(row["chosen"]["ndcg@10"], 4), flush=True)
    return result


def _distinct(values: list[float]) -> list[float]:
    return sorted({round(value, 4) for value in values})


def summarise(directory: Path) -> dict:
    runs = [json.loads(path.read_text(encoding="utf-8")) for path in sorted(directory.rglob("*.json"))]
    sides = {"vnni": [r for r in runs if r["cpu"]["vnni"]], "no_vnni": [r for r in runs if not r["cpu"]["vnni"]]}
    summary: dict = {
        "jobs": {side: len(members) for side, members in sides.items()},
        "cpus": sorted({f"{r['cpu']['model']}, VNNI {r['cpu']['vnni']}, AVX-512 {r['cpu']['avx512']}" for r in runs}),
        "variants": {},
    }
    for name in runs[0]["variants"]:
        row: dict = {}
        for side, members in sides.items():
            cells = [run["variants"][name] for run in members]
            floats = [run["variants"][FLOAT] for run in members]
            row[side] = {
                "gate_ndcg": _distinct([c["gate"]["ndcg@10"] for c in cells]),
                "gate_recall": _distinct([c["gate"]["recall@20"] for c in cells]),
                "chosen_ndcg": _distinct([c["chosen"]["ndcg@10"] for c in cells]),
                "coverage_ndcg": _distinct([c["chosen_coverage"]["ndcg@10"] for c in cells]),
                "known_ndcg": _distinct([c["chosen_known"]["ndcg@10"] for c in cells]),
                "cosine_to_float_mean": statistics.mean(c["cosine_to_float_mean"] for c in cells),
                "cosine_to_float_min": min(c["cosine_to_float_min"] for c in cells),
                "query_ms_p50": statistics.median(c["query_ms_p50"] for c in cells),
                "distinct_vectors": len({c["vector_sha"] for c in cells}),
                "chosen_vs_float": list(
                    paired_difference_ci(cells[0]["chosen"]["per_query"], floats[0]["chosen"]["per_query"])
                ),
                "coverage_vs_float": list(
                    paired_difference_ci(
                        cells[0]["chosen_coverage"]["per_query"], floats[0]["chosen_coverage"]["per_query"]
                    )
                ),
            }
        one = np.array(sides["vnni"][0]["variants"][name]["vectors"])
        other = np.array(sides["no_vnni"][0]["variants"][name]["vectors"])
        across = (one * other).sum(axis=1) / (np.linalg.norm(one, axis=1) * np.linalg.norm(other, axis=1))
        row["cross_cpu_cosine_mean"] = float(across.mean())
        row["cross_cpu_cosine_min"] = float(across.min())
        for metric in ("gate_ndcg", "chosen_ndcg", "coverage_ndcg", "known_ndcg"):
            row[f"{metric}_gap"] = row["no_vnni"][metric][0] - row["vnni"][metric][0]
        summary["variants"][name] = row
    return summary


def report(summary: dict) -> str:
    def cell(values: list[float]) -> str:
        return " to ".join(f"{value:.4f}" for value in values) if len(values) > 1 else f"{values[0]:.4f}"

    def pair(values: list[float]) -> str:
        verdict = "better" if values[1] > 0 else ("worse" if values[2] < 0 else "no clear difference")
        return f"{values[0]:+.3f} [{values[1]:+.3f}, {values[2]:+.3f}], {verdict}"

    lines = [
        f"Jobs: {summary['jobs']}",
        *summary["cpus"],
        "",
        "| Variant | Chosen, VNNI | Chosen, none | Gate, VNNI | Gate, none | Coverage, VNNI | Coverage, none "
        "| Cross-CPU cosine, mean / min | vs float, VNNI | vs float, none | ms p50, VNNI / none |",
        "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for name, row in summary["variants"].items():
        yes, no = row["vnni"], row["no_vnni"]
        lines.append(
            f"| {name} | {cell(yes['chosen_ndcg'])} | {cell(no['chosen_ndcg'])} | {cell(yes['gate_ndcg'])} | "
            f"{cell(no['gate_ndcg'])} | {cell(yes['coverage_ndcg'])} | {cell(no['coverage_ndcg'])} | "
            f"{row['cross_cpu_cosine_mean']:.5f} / {row['cross_cpu_cosine_min']:.5f} | {pair(yes['chosen_vs_float'])} | "
            f"{pair(no['chosen_vs_float'])} | {yes['query_ms_p50']:.1f} / {no['query_ms_p50']:.1f} |"
        )
    return chr(10).join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--out", type=Path)
    parser.add_argument("--models", type=Path)
    parser.add_argument("--report", type=Path)
    parser.add_argument("--json", type=Path)
    args = parser.parse_args()
    if args.report:
        summary = summarise(args.report)
        if args.json:
            args.json.write_text(json.dumps(summary, indent=1) + chr(10), encoding="utf-8", newline=chr(10))
        print(report(summary))
        return
    if args.models:
        _use_local_models(args.models.resolve())
    args.out.write_text(json.dumps(measure()), encoding="utf-8")


if __name__ == "__main__":
    main()
