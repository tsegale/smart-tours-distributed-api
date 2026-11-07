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
SHIPPED = "paraphrase-ml-uint8"
OLD = "paraphrase-ml-int8"  # shipped until issue #10: its quality depended on the CPU
CHOSEN = f"hybrid+parser:{SHIPPED}:w0.5"
OLD_CHOSEN = f"hybrid+parser:{OLD}:w0.5"
SETS = ("curated", "rule", "known", "coverage")


def load(name: str):
    return json.loads((RESULTS / name).read_text(encoding="utf-8"))


def interval(triple: list[float]) -> str:
    return f"{triple[0]:.3f} [{triple[1]:.3f}, {triple[2]:.3f}]"


def shapes_row(models: dict, before: dict, label: str, name: str | None = None) -> str:
    """Latency comes from the earlier run: labels can't change it, and the later run shared a busy laptop."""
    row = models["systems"][label]
    timing = before["systems"].get(label) or row["curated"]
    cells = " | ".join(interval(row[s]["ndcg"]) for s in SETS)
    return f"| {name or label} | {cells} | {timing['ms_p50']:.0f} / {timing['ms_p95']:.0f} |"


def table(header: list[str], rows: list[str]) -> str:
    return "\n".join(["| " + " | ".join(header) + " |", "| " + " | ".join("---" for _ in header) + " |", *rows])


MODES = [
    ("lexical", "Lexical only"),
    (f"dense:{SHIPPED}", "Dense only"),
    (f"hybrid:{SHIPPED}:k60", "Hybrid, equal weights, k = 60"),
    (f"hybrid:{SHIPPED}:k10", "Hybrid, equal weights, k = 10"),
    (f"hybrid:{SHIPPED}:w0.5", "Hybrid, lexical weight 0.5"),
    (f"dense+parser:{SHIPPED}", "Dense + parser"),
    (f"hybrid+parser:{SHIPPED}", "Hybrid + parser, equal weights"),
    (CHOSEN, "**Hybrid + parser, lexical weight 0.5 (chosen)**"),
    (f"hybrid+parser+rerank:{SHIPPED}:en-de-l6-uint8", "... + EN-DE cross-encoder"),
    (f"hybrid+parser+rerank:{SHIPPED}:ms-marco-l6-uint8", "... + EN cross-encoder"),
]
COMPARED = [
    ("lexical", "Lexical only"),
    (f"dense+parser:{SHIPPED}", "Dense + parser"),
    (f"hybrid+parser:{SHIPPED}", "Equal weights"),
    (f"hybrid+parser+rerank:{SHIPPED}:en-de-l6-uint8", "With EN-DE cross-encoder"),
    (f"hybrid+parser+rerank:{SHIPPED}:ms-marco-l6-uint8", "With EN cross-encoder"),
    ("hybrid+parser:paraphrase-ml:w0.5", "Float instead of U8U8"),
    (OLD_CHOSEN, "The previous int8 file, on a VNNI CPU"),
    ("hybrid+parser:e5-small-int8:w0.5", "e5-small int8"),
    ("hybrid+parser:bge-small-en:w0.5", "bge-small-en"),
    ("hybrid+parser:minilm-l6:w0.5", "all-MiniLM-L6-v2"),
]


def _old(label: str) -> str:
    """The same system on the int8 file: what the label reviews were measured on."""
    return label.replace(SHIPPED, OLD).replace("l6-uint8", "l6-int8")


def verdict(low: float, high: float) -> str:
    return "better" if low > 0 else ("worse" if high < 0 else "no clear difference")


def modes(models: dict, before: dict) -> str:
    header = ["System", "Curated: vague", "Rule: filters", "Known: exact name", "Coverage", "ms p50 / p95"]
    return table(header, [shapes_row(models, before, label, name) for label, name in MODES])


def paired(models: dict) -> str:
    rows = []
    per_query = models["per_query"]
    for label, name in COMPARED:
        cells = []
        for set_name in SETS:
            diff, low, high = paired_difference_ci(
                per_query[label][set_name]["ndcg"], per_query[CHOSEN][set_name]["ndcg"]
            )
            cells.append(f"{diff:+.3f} [{low:+.3f}, {high:+.3f}], {verdict(low, high)}")
        rows.append(f"| {name} | " + " | ".join(cells) + " |")
    return table(["Against the chosen system", "Curated", "Rule", "Known", "Coverage"], rows)


def review(models: dict, before: dict) -> str:
    """Every system's curated score under Claude's labels and the owner's. Other sets' labels didn't change: checked."""
    rows = []
    for label, row in models["systems"].items():
        old = before["systems"].get(label)
        if old is None:
            continue  # a model added after the review
        for set_name in ("rule", "known"):
            if abs(row[set_name]["ndcg"][0] - old[set_name]) > 1e-9:
                raise SystemExit(f"{label} {set_name} moved, and its labels didn't: this isn't only the review")
        new = row["curated"]["ndcg"]
        rows.append(f"| {label} | {old['ndcg'][0]:.3f} | {interval(new)} | {new[0] - old['ndcg'][0]:+.3f} |")
    return table(["System", "Before", "After [95% CI]", "Change"], rows)


def _difference(per_query: dict, label: str, reference: str) -> tuple[float, float, float]:
    return paired_difference_ci(per_query[label]["curated"]["ndcg"], per_query[reference]["curated"]["ndcg"])


def decisions(models: dict, before: dict) -> str:
    """Each decision's comparison on the curated set, under both sets of labels, as a paired difference."""
    k_label, k_reference = f"hybrid:{OLD}:k10", f"hybrid:{OLD}:k60"
    comparisons = [(_old(label), name, OLD_CHOSEN) for label, name in COMPARED if label != OLD_CHOSEN]
    comparisons.append((k_label, "k = 10 against k = 60, equal weights", k_reference))
    rows = []
    for label, name, reference in comparisons:
        old = before["paired"][f"{label} vs {reference}"]
        new = _difference(models["per_query"], label, reference)
        changed = "**yes**" if verdict(*old[1:]) != verdict(*new[1:]) else "no"
        rows.append(
            f"| {name} | {old[0]:+.3f}, {verdict(*old[1:])} | "
            f"{new[0]:+.3f} [{new[1]:+.3f}, {new[2]:+.3f}], {verdict(*new[1:])} | {changed} |"
        )
    return table(["Against the system chosen then", "Before", "After", "Verdict changed"], rows)


def coverage(models: dict) -> str:
    rows = []
    for label, name in [*MODES, *COMPARED[5:]]:
        row = models["systems"][label]["coverage"]
        rows.append(
            f"| {name} | {interval(row['ndcg'])} | {row['ndcg_en'][0]:.3f} | {row['ndcg_de'][0]:.3f} | "
            f"{row['recall'][0]:.3f} |"
        )
    return table(["System", "nDCG@10", "EN", "DE", "Recall@20"], rows)


def coverage_review(models: dict, before: dict) -> str:
    """Every system's coverage score under Claude's labels and the owner's, on the int8 file both were measured on."""
    rows = []
    for label, old in before["systems"].items():
        new = models["systems"][label]["coverage"]["ndcg"]
        rows.append(f"| {label} | {old['ndcg'][0]:.3f} | {interval(new)} | {new[0] - old['ndcg'][0]:+.3f} |")
    return table(["System", "Before", "After [95% CI]", "Change"], rows)


def _pair(values: list[float]) -> str:
    return f"{values[0]:+.3f} [{values[1]:+.3f}, {values[2]:+.3f}], {verdict(*values[1:])}"


def coverage_gap(models: dict, before: dict, old: dict, new: dict) -> str:
    """Does dense's lead over the chosen system survive the review? On coverage, and on the 50 reviewed queries."""
    rows = []
    for name, candidate, label in (
        ("Dense, no parser", "dense, no parser", "dense:"),
        ("Dense + parser", "dense + parser (no lexical list)", "dense+parser:"),
    ):
        rows.append(
            f"| {name} | {_pair(before['paired'][label + OLD])} | "
            f"{_pair(old['summary'][candidate]['coverage']['paired'])} | "
            f"{_pair(new['summary'][candidate]['coverage']['paired'])} | "
            f"{_pair(new['summary'][candidate]['curated']['paired'])} |"
        )
    header = [
        "Minus the chosen system",
        "Coverage, Claude's labels, int8",
        "Coverage, owner's labels, int8",
        "Coverage, owner's labels, U8U8",
        "The 50 reviewed queries, U8U8",
    ]
    return table(header, rows)


def coverage_queries(analysis: dict) -> str:
    rows = []
    for row in analysis["detail"]["coverage"]:
        words = sorted({word for tour in row["promoted"] for word in tour["matched"]})
        promoted = [tour["grade"] for tour in row["promoted"]]
        displaced = [tour["grade"] for tour in row["displaced"]]
        rows.append(
            f"| {row['q']} | {row['tokens']} | {'yes' if row['negated'] else ''} | {row['chosen']:.3f} | "
            f"{row['dense']:.3f} | {row['chosen'] - row['dense']:+.3f} | {row['lexical']:.3f} | "
            f"{', '.join(map(str, promoted))} | "
            f"{', '.join(map(str, displaced))} | {', '.join(words)} |"
        )
    header = [
        "Query",
        "Words",
        "Negated",
        "Chosen",
        "Dense + parser",
        "Lexical's effect",
        "Lexical alone",
        "Grades it pushed in",
        "Grades it pushed out",
        "Words that matched",
    ]
    return table(header, rows)


def coverage_groups(analysis: dict) -> str:
    def grade(value) -> str:
        return "-" if value is None else f"{value:.2f}"

    rows = [
        f"| {name} | {g['queries']} | {g['tokens']:.1f} | {g['chosen']:.3f} | {g['dense']:.3f} | "
        f"{g['dense_minus_chosen']:+.3f} | {g['promoted']} | {grade(g['promoted_mean_grade'])} | "
        f"{grade(g['displaced_mean_grade'])} |"
        for name, g in analysis["groups"].items()
    ]
    header = [
        "Queries",
        "n",
        "Mean words",
        "Chosen",
        "Dense + parser",
        "Dense minus chosen",
        "Tours lexical pushed in",
        "Their mean grade",
        "Mean grade of those pushed out",
    ]
    return table(header, rows)


def coverage_candidates(analysis: dict) -> str:
    rows = []
    for name, by_set in analysis["summary"].items():
        cells = []
        for set_name in ("curated", "coverage", "rule", "known", "long titles"):
            cell = by_set[set_name]
            text = f"{cell['ndcg'][0]:.3f}"
            if "paired" in cell:
                text += f", {_pair(cell['paired'])}"
            cells.append(text)
        rows.append(f"| {name} | " + " | ".join(cells) + " |")
    return table(["Candidate", "Curated", "Coverage", "Rule", "Known", "Long titles"], rows)


def drift(summary: dict) -> str:
    def cell(values: list[float]) -> str:
        return " to ".join(f"{value:.4f}" for value in values) if len(values) > 1 else f"{values[0]:.4f}"

    rows = []
    for name, row in summary["variants"].items():
        yes, no = row["vnni"], row["no_vnni"]
        rows.append(
            f"| {name} | {cell(yes['chosen_ndcg'])} | {cell(no['chosen_ndcg'])} | {cell(yes['gate_ndcg'])} | "
            f"{cell(no['gate_ndcg'])} | {row['cross_cpu_cosine_mean']:.5f} / {row['cross_cpu_cosine_min']:.5f} | "
            f"{no['cosine_to_float_min']:.4f} | {_pair(yes['chosen_vs_float'])} | {_pair(no['chosen_vs_float'])} | "
            f"{yes['query_ms_p50']:.1f} / {no['query_ms_p50']:.1f} |"
        )
    header = [
        "Variant",
        "Chosen system, VNNI",
        "Chosen system, no VNNI",
        "Gate, VNNI",
        "Gate, no VNNI",
        "Cosine across CPUs, mean / min",
        "Worst cosine to float, no VNNI",
        "Against float, VNNI",
        "Against float, no VNNI",
        "Query ms p50, VNNI / no VNNI",
    ]
    return table(header, rows)


def models_table(models: dict, footprint: dict) -> str:
    rows = []
    files = {
        "minilm-l6": ("all-MiniLM-L6-v2", "EN", 90),
        "bge-small-en": ("bge-small-en-v1.5", "EN", 133),
        "paraphrase-ml": ("paraphrase-multilingual-MiniLM-L12-v2", "50", 470),
        OLD: ("same, previous int8 file", "50", 118),
        SHIPPED: ("same, U8U8 (shipped)", "50", 118),
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
            f"{row['coverage']['ndcg'][0]:.3f} | {curated['recall'][0]:.3f} | {memory} | {latency} | {megabytes} |"
        )
    header = [
        "Model (hybrid + parser, weight 0.5)",
        "Languages",
        "Curated nDCG@10",
        "EN",
        "DE",
        "Rule",
        "Known",
        "Coverage",
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


def cross_encoders(survey: dict) -> str:
    rows = []
    for name, row in survey["rerankers"].items():
        yes, no = row["vnni"], row["no_vnni"]
        rows.append(
            f"| {name} | {yes['ndcg@10'][0]:.4f} | {no['ndcg@10'][0]:.4f} | {row['score_difference_mean']:.3f} | "
            f"{row['score_difference_max']:.3f} | {yes['ms_p50']:.0f} / {no['ms_p50']:.0f} |"
        )
    header = [
        "Cross-encoder file",
        "nDCG@10, VNNI",
        "nDCG@10, no VNNI",
        "Mean score difference across CPUs",
        "Largest",
        "ms p50, VNNI / no VNNI",
    ]
    return table(header, rows)


def gate_lines(survey: dict) -> str:
    rows = []
    for name, line in survey["gate"][SHIPPED].items():
        if line["spread"]:
            rows.append(f"| {name} | {line['vnni'][0]:.4f} | {line['no_vnni'][0]:.4f} | {line['spread']:.4f} |")
    return table(["Gate line, shipped model", "VNNI", "No VNNI", "Spread"], rows)


def heldout(results: dict) -> str:
    """Every candidate on every set, the held-out one included, against the current system."""
    order = ("curated", "rule", "known", "coverage", "heldout", "titles")
    rows = []
    for name, row in results["systems"].items():
        cells = []
        for set_name in order:
            cell = row["sets"][set_name]
            value = f"{cell['ndcg'][0]:.3f}"
            if "paired" in cell:
                value += f", {_pair(cell['paired'])}"
            cells.append(value)
        rows.append(f"| {name} | " + " | ".join(cells) + " |")
    header = ["Candidate", "Curated", "Rule", "Known", "Coverage", "**Held-out**", "Titles"]
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
    before = load("curated_before_review.json")
    coverage_before = load("coverage_before_review.json")
    analysis = load("coverage_analysis.json")
    analysis_old = load("coverage_analysis_old_int8.json")
    lines = (RESULTS / "footprint.jsonl").read_text(encoding="utf-8").splitlines()
    footprint_rows = [json.loads(line) for line in lines if line.strip()]
    onnx = {row["model"]: row for row in footprint_rows if row["runtime"] == "onnx"}
    fill(
        DOCS / "search-quality.md",
        {
            "modes": modes(models, before),
            "paired": paired(models),
            "review": review(models, before),
            "decisions": decisions(models, before),
            "coverage": coverage(models),
            "coverage-review": coverage_review(models, coverage_before),
            "coverage-gap": coverage_gap(models, coverage_before, analysis_old, analysis),
            "coverage-queries": coverage_queries(analysis),
            "coverage-groups": coverage_groups(analysis),
            "coverage-candidates": coverage_candidates(analysis),
            "heldout": heldout(load("lexical_precision.json")),
            "drift": drift(load("cpu_survey.json")),
            "gate-lines": gate_lines(load("cpu_survey_gate.json")),
            "cross-encoders": cross_encoders(load("cpu_survey_gate.json")),
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
