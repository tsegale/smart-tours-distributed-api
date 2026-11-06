#!/usr/bin/env python3
"""What one embedding model costs to run, in a process of its own so that nothing else is counted.

    python -m eval.footprint --model e5-small-int8 --runtime onnx
    python -m eval.footprint --model minilm-l6 --runtime torch   (needs sentence-transformers and torch)

Prints one JSON line: resident memory after loading and after embedding, query and document latency on this machine's
CPU. Run it on a quiet machine: these are the numbers that decide a runtime.
"""

import argparse
import json
import time
from pathlib import Path

import psutil

from app.embeddings import MODELS, OnnxEmbedder, SentenceTransformerEmbedder, document_text

from .metrics import percentile

DATASET = Path(__file__).resolve().parents[2] / "datasets" / "tours_v1" / "tours.jsonl"
QUERIES = [
    "abandoned town swallowed by the desert",
    "verlassene Stadt in der Wüste",
    "see dead trees on a white clay floor",
    "kleine Wüstentiere wie Schlangen und Eidechsen",
    "rhinos at night while sitting down",
]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", required=True, choices=list(MODELS))
    parser.add_argument("--runtime", required=True, choices=["onnx", "torch"])
    parser.add_argument("--documents", type=int, default=100)
    args = parser.parse_args()
    spec = MODELS[args.model]
    process = psutil.Process()
    baseline = process.memory_info().rss

    began = time.perf_counter()
    if args.runtime == "onnx":
        embedder = OnnxEmbedder(args.model, spec)
    else:
        embedder = SentenceTransformerEmbedder(spec.repo, 384, spec.query_prefix, spec.document_prefix)
    embedder.warm_up()
    load_s = time.perf_counter() - began
    loaded = process.memory_info().rss

    timings = []
    for _ in range(40):
        for query in QUERIES:
            began = time.perf_counter()
            embedder.embed_query(query)
            timings.append((time.perf_counter() - began) * 1000)

    with DATASET.open(encoding="utf-8") as lines:
        tours = [tour for tour in map(json.loads, lines) if tour["authored"]][: args.documents]
    contents = [document_text(t["name"], t["description"]) for t in tours]
    began = time.perf_counter()
    for content in contents:
        embedder.embed_document(content)
    per_document_ms = (time.perf_counter() - began) / len(contents) * 1000

    print(
        json.dumps(
            {
                "model": args.model,
                "runtime": args.runtime,
                "load_s": round(load_s, 2),
                "rss_baseline_mb": baseline // 2**20,
                "rss_loaded_mb": loaded // 2**20,
                "rss_after_mb": process.memory_info().rss // 2**20,
                "query_p50_ms": round(percentile(timings, 0.5), 2),
                "query_p95_ms": round(percentile(timings, 0.95), 2),
                "document_ms": round(per_document_ms, 2),
                "documents": len(contents),
            }
        )
    )


if __name__ == "__main__":
    main()
