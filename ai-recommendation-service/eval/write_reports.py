#!/usr/bin/env python3
"""Fill the benchmark documents' tables from eval/results, so that no number in them is typed by hand.

    python -m eval.write_reports

Each document holds marker pairs: <!-- table:name --> ... <!-- /table:name -->. Only what's between them is replaced.
"""

import json
import re
from pathlib import Path

from .metrics import paired_difference_ci

RESULTS = Path(__file__).with_name("results")
DOCS = Path(__file__).resolve().parents[2] / "docs" / "benchmarks"
CHOSEN = "hybrid+parser:paraphrase-ml-int8:w0.5"
SETS = ("curated", "rule", "known")


def load(name: str):
    return json.loads((RESULTS / name).read_text(encoding="utf-8"))


def interval(triple: list[float]) -> str:
    return f"{triple[0]:.3f} [{triple[1]:.3f}, {triple[2]:.3f}]"


def shapes_row(models: dict, label: str, name: str | None = None) -> str:
    row = models["systems"][label]
    cells = " | ".join(interval(row[s]["ndcg"]) for s in SETS)
    return f"| {name or label} | {cells} | {row['curated']['ms_p50']:.0f} / {row['curated']['ms_p95']:.0f} |"


def table(header: list[str], rows: list[str]) -> str:
    return "\n".join(["| " + " | ".join(header) + " |", "| " + " | ".join("---" for _ in header) + " |", *rows])


def modes(models: dict) -> str:
    rows = [
        ("lexical", "Lexical only"),
        ("dense:paraphrase-ml-int8", "Dense only"),
        ("hybrid:paraphrase-ml-int8:k60", "Hybrid, equal weights, k = 60"),
        ("hybrid:paraphrase-ml-int8:k10", "Hybrid, equal weights, k = 10"),
        ("hybrid:paraphrase-ml-int8:w0.5", "Hybrid, lexical weight 0.5"),
        ("dense+parser:paraphrase-ml-int8", "Dense + parser"),
        ("hybrid+parser:paraphrase-ml-int8", "Hybrid + parser, equal weights"),
        (CHOSEN, "**Hybrid + parser, lexical weight 0.5 (chosen)**"),
        ("hybrid+parser+rerank:paraphrase-ml-int8:en-de-l6-int8", "... + EN-DE cross-encoder"),
        ("hybrid+parser+rerank:paraphrase-ml-int8:ms-marco-l6-int8", "... + EN cross-encoder"),
    ]
    header = ["System", "Curated: vague", "Rule: filters", "Known: exact name", "ms p50 / p95"]
    return table(header, [shapes_row(models, label, name) for label, name in rows])


def paired(models: dict) -> str:
    rows = []
    compared = [
        ("lexical", "Lexical only"),
        ("dense+parser:paraphrase-ml-int8", "Dense + parser"),
        ("hybrid+parser:paraphrase-ml-int8", "Equal weights"),
        ("hybrid+parser+rerank:paraphrase-ml-int8:en-de-l6-int8", "With EN-DE cross-encoder"),
        ("hybrid+parser+rerank:paraphrase-ml-int8:ms-marco-l6-int8", "With EN cross-encoder"),
        ("hybrid+parser:paraphrase-ml:w0.5", "Float instead of int8"),
        ("hybrid+parser:e5-small-int8:w0.5", "e5-small int8"),
        ("hybrid+parser:bge-small-en:w0.5", "bge-small-en"),
        ("hybrid+parser:minilm-l6:w0.5", "all-MiniLM-L6-v2"),
    ]
    per_query = models["per_query"]
    for label, name in compared:
        cells = []
        for set_name in SETS:
            diff, low, high = paired_difference_ci(
                per_query[label][set_name]["ndcg"], per_query[CHOSEN][set_name]["ndcg"]
            )
            verdict = "better" if low > 0 else ("worse" if high < 0 else "no clear difference")
            cells.append(f"{diff:+.3f} [{low:+.3f}, {high:+.3f}], {verdict}")
        rows.append(f"| {name} | " + " | ".join(cells) + " |")
    return table(["Against the chosen system", "Curated", "Rule", "Known"], rows)


def models_table(models: dict, footprint: dict) -> str:
    rows = []
    files = {
        "minilm-l6": ("all-MiniLM-L6-v2", "EN", 90),
        "bge-small-en": ("bge-small-en-v1.5", "EN", 133),
        "paraphrase-ml": ("paraphrase-multilingual-MiniLM-L12-v2", "50", 470),
        "paraphrase-ml-int8": ("same, int8", "50", 118),
        "e5-small-int8": ("multilingual-e5-small, int8", "94", 118),
    }
    for key, (name, languages, megabytes) in files.items():
        row = models["systems"][f"hybrid+parser:{key}:w0.5"]
        windows = footprint.get(key, {})
        memory = f"{windows['rss_after_mb']}" if windows else "-"
        latency = f"{windows['query_p50_ms']:.1f}" if windows else "-"
        curated = row["curated"]
        rows.append(
            f"| {name} | {languages} | {interval(curated['ndcg'])} | {curated['ndcg_en'][0]:.3f} | "
            f"{curated['ndcg_de'][0]:.3f} | {row['rule']['ndcg'][0]:.3f} | {row['known']['ndcg'][0]:.3f} | "
            f"{curated['recall'][0]:.3f} | {memory} | {latency} | {megabytes} |"
        )
    header = [
        "Model (hybrid + parser, weight 0.5)",
        "Languages",
        "Curated nDCG@10",
        "EN",
        "DE",
        "Rule",
        "Known",
        "Curated recall@20",
        "RSS MB",
        "Query ms p50",
        "File MB",
    ]
    return table(header, rows)


def runtimes(footprint_rows: list[dict]) -> str:
    rows = []
    for row in footprint_rows:
        load_s = "download included" if row["load_s"] > 1000 else f"{row['load_s']:.1f}"
        rows.append(
            f"| {row['model']} | {row['runtime']} | {row['rss_after_mb']} | {row['query_p50_ms']:.1f} | "
            f"{row['query_p95_ms']:.1f} | {row['document_ms']:.1f} | {load_s} |"
        )
    header = ["Model", "Runtime", "RSS MB", "Query p50 ms", "Query p95 ms", "ms per document", "Load s"]
    return table(header, rows)


def itineraries(results: list[dict]) -> str:
    names = {
        "none": "Deterministic only (no provider)",
        "fake:good": "Fake: always valid",
        "fake:repair": "Fake: invalid, then valid",
        "fake:invalid": "Fake: never valid",
        "fake:injected": "Fake: obeys an injected instruction",
    }

    def share(value) -> str:
        return "-" if value is None else f"{value:.0%}"

    rows = [
        f"| {names.get(r['provider'], r['provider'])} | {r['constraint_satisfaction']:.0%} | "
        f"{share(r['first_pass_valid'])} | "
        f"{share(r['repair_rate'])} | {share(r['fallback_rate'])} | {r['ms_p50']:.0f} / {r['ms_p95']:.0f} | "
        f"{r['tokens_per_plan']:.0f} | {r['items_per_plan']:.1f} | {r['day_coverage']:.0%} | {r['budget_used']:.0%} |"
        for r in results
    ]
    header = [
        "Provider",
        "Constraints met",
        "First-pass valid",
        "Repaired",
        "Fell back",
        "ms p50 / p95",
        "Tokens per plan",
        "Items per plan",
        "Days covered",
        "Budget used",
    ]
    return table(header, rows)


def recommendations(results: dict) -> str:
    rows = [
        f"| {name} | {interval(row['hit_rate@10'])} | {interval(row['ndcg@10'])} | "
        f"{row['taste_match@10'][0]:.2f} | {row['catalogue_coverage']:.1%} |"
        for name, row in results["systems"].items()
    ]
    return table(["System", "Hit rate@10", "nDCG@10", "Taste match@10", "Catalogue coverage"], rows)


def fill(path: Path, tables: dict[str, str]) -> None:
    text = path.read_text(encoding="utf-8")
    for name, content in tables.items():
        pattern = re.compile(rf"(<!-- table:{name} -->\s).*?(<!-- /table:{name} -->)", re.DOTALL)
        text, count = pattern.subn(lambda m, c=content: m.group(1) + c + chr(10) + m.group(2), text)
        if count != 1:
            raise SystemExit(f"{path.name} has no markers for {name}")
    path.write_text(text, encoding="utf-8", newline="\n")


def main() -> None:
    models = load("models.json")
    lines = (RESULTS / "footprint.jsonl").read_text(encoding="utf-8").splitlines()
    footprint_rows = [json.loads(line) for line in lines if line.strip()]
    onnx = {row["model"]: row for row in footprint_rows if row["runtime"] == "onnx"}
    fill(
        DOCS / "search-quality.md",
        {
            "modes": modes(models),
            "paired": paired(models),
            "models": models_table(models, onnx),
            "runtimes": runtimes(footprint_rows),
        },
    )
    fill(
        DOCS / "itineraries.md",
        {
            "itineraries": itineraries(load("itineraries.json")),
            "recommendations": recommendations(load("recommendations.json")),
        },
    )


if __name__ == "__main__":
    main()
