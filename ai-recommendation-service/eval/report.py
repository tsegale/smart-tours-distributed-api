#!/usr/bin/env python3
"""Markdown tables from a run_eval result, with paired comparisons, so that the report quotes the run and nothing else.

python -m eval.report eval/results/models.json --baseline hybrid+parser:e5-small
"""

import argparse
import json
from pathlib import Path

from .metrics import paired_difference_ci


def _interval(triple: list[float]) -> str:
    return f"{triple[0]:.3f} [{triple[1]:.3f}, {triple[2]:.3f}]"


def systems_table(results: dict, set_name: str) -> str:
    lines = [
        "| System | nDCG@10 [95% CI] | Recall@20 | MRR | nDCG@10 EN | nDCG@10 DE | p50 ms | p95 ms |",
        "| --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for label, by_set in results["systems"].items():
        row = by_set.get(set_name)
        if not row:
            continue
        english = f"{row['ndcg_en'][0]:.3f}" if "ndcg_en" in row else "-"
        german = f"{row['ndcg_de'][0]:.3f}" if "ndcg_de" in row else "-"
        lines.append(
            f"| {label} | {_interval(row['ndcg'])} | {row['recall'][0]:.3f} | {row['mrr'][0]:.3f} "
            f"| {english} | {german} | {row['ms_p50']:.0f} | {row['ms_p95']:.0f} |"
        )
    return "\n".join(lines)


def paired_table(results: dict, set_name: str, baseline: str) -> str:
    """Against one system, query by query: much tighter than comparing two separate intervals."""
    reference = results["per_query"][baseline][set_name]["ndcg"]
    lines = [f"| System minus {baseline} | nDCG@10 difference [95% CI] | |", "| --- | --- | --- |"]
    for label, by_set in results["per_query"].items():
        if label == baseline or set_name not in by_set:
            continue
        difference, low, high = paired_difference_ci(by_set[set_name]["ndcg"], reference)
        verdict = "better" if low > 0 else ("worse" if high < 0 else "no clear difference")
        lines.append(f"| {label} | {difference:+.3f} [{low:+.3f}, {high:+.3f}] | {verdict} |")
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("results", type=Path)
    parser.add_argument("--baseline")
    args = parser.parse_args()
    results = json.loads(args.results.read_text(encoding="utf-8"))
    print("Queries:", results["queries"])
    if results.get("models"):
        print("\n| Model | load s | embed s | query embed ms |\n| --- | --- | --- | --- |")
        for name, row in results["models"].items():
            print(f"| {name} | {row['load_s']:.1f} | {row['embed_s']:.0f} | {row['query_embed_ms']:.1f} |")
    for set_name in results["queries"]:
        print(f"\n### {set_name}\n")
        print(systems_table(results, set_name))
        if args.baseline:
            print()
            print(paired_table(results, set_name, args.baseline))


if __name__ == "__main__":
    main()
