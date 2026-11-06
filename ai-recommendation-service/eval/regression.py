#!/usr/bin/env python3
"""The CI gate: retrieval quality on a fixed subset must not fall below the committed baseline.

Curated queries over the written tours only: 105 documents embed in seconds on a CI runner, against a cached ONNX model.
No LLM, no paid API. Run from ai-recommendation-service, with DATABASE_URL pointing at a migrated, disposable database:

    python -m eval.regression              compare with eval/baseline.json, exit 1 on a regression
    python -m eval.regression --update     rewrite the baseline, for a change that is meant to move it

The tolerance exists for arithmetic differences between CPUs, not to forgive a slide: quality moves in the baseline's
diff, where a reviewer can see it.
"""

import argparse
import json
import sys
from pathlib import Path

from sqlalchemy import text

from app.config import get_settings
from app.database import SessionLocal
from app.embeddings import MODELS
from app.retrieval import search

from .cpu import cpu_features
from .metrics import ndcg_at, recall_at
from .run_eval import curated_queries, embed_corpus, load_read_model, load_tours

BASELINE = Path(__file__).with_name("baseline.json")
TOLERANCE = 0.005
DRIFT_ISSUE = "https://github.com/tsegale/smart-tours-distributed-api/issues/10"


def drift_notice(failures: list[str]) -> list[str]:
    """Vector scores have differed between runners on one commit, so say so until that's fixed. Lexical can't drift."""
    if not any(failure.startswith("hybrid") for failure in failures):
        return []
    message = f"possible CPU-dependent int8 drift; see issue {DRIFT_ISSUE}"
    return [message, f"::error title=Retrieval gate::{message}"]


def measure(model: str) -> dict:
    tours = [tour for tour in load_tours() if tour["authored"]]
    load_read_model(tours)
    with SessionLocal() as db, db.begin():
        db.execute(text("DELETE FROM tour_embeddings"))
    embedder = embed_corpus(tours, model)["embedder"]
    queries = curated_queries(tours)
    results: dict[str, list[list[int]]] = {"lexical": [], "hybrid": []}
    for query in queries:
        vector = embedder.embed_query(query["q"])
        with SessionLocal() as db, db.begin():
            lexical = search(db, query_text=query["q"], query_vector=None, model=model, mode="lexical", limit=20)
            hybrid = search(db, query_text=query["q"], query_vector=vector, model=model, mode="hybrid", limit=20)
        results["lexical"].append([hit.tour_id for hit in lexical])
        results["hybrid"].append([hit.tour_id for hit in hybrid])
    summary: dict = {"model": model, "queries": len(queries)}
    for system, rankings in results.items():
        pairs = list(zip(rankings, (query["judgments"] for query in queries), strict=True))
        summary[system] = {
            "ndcg@10": round(sum(ndcg_at(r, j, 10) for r, j in pairs) / len(pairs), 4),
            "recall@20": round(sum(recall_at(r, j, 20) for r, j in pairs) / len(pairs), 4),
        }
    return summary


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--update", action="store_true")
    parser.add_argument("--model", choices=list(MODELS))
    args = parser.parse_args()
    baseline = json.loads(BASELINE.read_text(encoding="utf-8")) if BASELINE.exists() else None
    model = args.model or (baseline["model"] if baseline else get_settings().embedding_model)
    current = measure(model)
    print(json.dumps({"cpu": cpu_features(), **current}, indent=1))
    if args.update:
        BASELINE.write_text(json.dumps(current, indent=1) + "\n", encoding="utf-8", newline="\n")
        return 0
    if baseline is None:
        print("No baseline: run with --update", file=sys.stderr)
        return 1
    failures = [
        f"{system} {metric}: {current[system][metric]} is below the baseline's {expected}"
        for system in ("lexical", "hybrid")
        for metric, expected in baseline[system].items()
        if current[system][metric] < expected - TOLERANCE
    ]
    for failure in failures:
        print("REGRESSION", failure, file=sys.stderr)
    for line in drift_notice(failures):
        print(line, file=sys.stderr)
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
