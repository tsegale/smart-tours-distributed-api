#!/usr/bin/env python3
"""Memory of the running compose stack: `docker stats --no-stream` per container, its limit, and the total.

    python benchmarks/memory_footprint.py --label before-phase-4

Run it with the stack idle and settled (a minute after `docker compose up --wait`), so start-up peaks don't
count. Writes benchmarks/out/memory-<label>.json and prints a Markdown table. Standard library only.
"""

from __future__ import annotations

import argparse
import json
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT_DIR = ROOT / "benchmarks" / "out"
UNITS = {"B": 1, "KiB": 1024, "MiB": 1024**2, "GiB": 1024**3, "kB": 1000, "MB": 1000**2, "GB": 1000**3}


def to_mib(text: str) -> float:
    text = text.strip()
    for unit in sorted(UNITS, key=len, reverse=True):
        if text.endswith(unit):
            return float(text[: -len(unit)]) * UNITS[unit] / 1024**2
    raise ValueError(f"unknown size {text!r}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--label", required=True)
    args = parser.parse_args()

    project = ROOT.name
    lines = subprocess.run(
        ["docker", "stats", "--no-stream", "--format", "{{.Name}}\t{{.MemUsage}}\t{{.CPUPerc}}"],
        capture_output=True, text=True, check=True,
    ).stdout.splitlines()  # fmt: skip
    rows = []
    for line in lines:
        name, usage, cpu = line.split("\t")
        if not name.startswith(project + "-"):
            continue
        used, limit = (part.strip() for part in usage.split("/"))
        service = name[len(project) + 1 :].rsplit("-", 1)[0]
        rows.append({"service": service, "usedMiB": round(to_mib(used), 1), "limitMiB": round(to_mib(limit), 1),
                     "cpu": cpu})  # fmt: skip
    rows.sort(key=lambda r: -r["usedMiB"])
    total_used = sum(r["usedMiB"] for r in rows)
    total_limit = sum(r["limitMiB"] for r in rows)
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    (OUT_DIR / f"memory-{args.label}.json").write_text(
        json.dumps({"rows": rows, "totalUsedMiB": total_used, "totalLimitMiB": total_limit}, indent=2))

    print(f"{args.label}: {len(rows)} containers")
    print("| Service | Used (MiB) | Limit (MiB) |")
    print("| --- | ---: | ---: |")
    for row in rows:
        print(f"| {row['service']} | {row['usedMiB']:.0f} | {row['limitMiB']:.0f} |")
    print(f"| **Total** | **{total_used:.0f}** | **{total_limit:.0f}** |")


if __name__ == "__main__":
    main()
