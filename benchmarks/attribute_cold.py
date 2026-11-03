#!/usr/bin/env python3
"""Where the time of a cold-cache tour detail goes: one short cold run, attributed by tour-core's stage timers.

Runs the `cold` scenario of run_integrations_benchmark.py once (weather cache emptied, healthy stubs, same k6
script and rate) and compares what k6 measured through the gateway with tour-core's own histograms over the
same run (the difference between two /actuator/prometheus scrapes):

    tour_detail_request_seconds        the whole request inside tour-core, first filter to last byte
    catalogue_cache_read_seconds       Redis read of the tour (cache=tour), hit or miss
    catalogue_cache_load_seconds       the database load on a miss
    tour_detail_stage_seconds          fx, weather (departure lookup, cache, provider wait), write
    jvm_gc_pause_seconds               garbage-collection pauses
    hikaricp_connections_acquire_seconds  waiting for a database connection
    http_client_requests_seconds       outbound calls to the provider stubs
    resilience4j_circuitbreaker_*      calls by outcome, and calls refused while a circuit was open

Whatever k6 saw beyond tour_detail_request was spent outside tour-core's request handling: the gateway, the
network, or waiting for a Tomcat thread. Needs the chaos profile, like the benchmark; doesn't touch the report.

    python benchmarks/attribute_cold.py --duration 30s [--fresh]
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import defaultdict
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "benchmarks"))
from dev_token import _secret_from_env_file, mint  # noqa: E402
from run_booking_contention import OUT_DIR, compose_network, env_file_value, sh  # noqa: E402
from run_integrations_benchmark import (  # noqa: E402
    check_stack,
    core,
    metric,
    prepare,
    run_k6,
    tours_psql,
    vm_memory,
    weather_lookups,
)

SAMPLE = re.compile(r"^([a-zA-Z_:][a-zA-Z0-9_:]*)(\{[^}]*\})?\s+(\S+)$")
WATCHED = (
    "tour_detail_request_seconds",
    "tour_detail_stage_seconds",
    "catalogue_cache_read_seconds",
    "catalogue_cache_load_seconds",
    "jvm_gc_pause_seconds",
    "hikaricp_connections_acquire_seconds",
    "http_client_requests_seconds",
    "resilience4j_circuitbreaker_calls_seconds",
    "resilience4j_circuitbreaker_not_permitted_calls_total",
)


def scrape() -> dict[tuple[str, str], float]:
    samples: dict[tuple[str, str], float] = {}
    for line in core("wget", "-qO-", "http://localhost:8081/actuator/prometheus").splitlines():
        match = SAMPLE.match(line)
        if match and match.group(1).startswith(WATCHED):
            samples[(match.group(1), match.group(2) or "")] = float(match.group(3))
    return samples


def labels_without_le(labels: str) -> str:
    stripped = re.sub(r',?le="[^"]*"', "", labels).replace("{,", "{")
    return "" if stripped == "{}" else stripped


def quantile(buckets: list[tuple[float, float]], q: float) -> float:
    """histogram_quantile over cumulative (upper bound, count) pairs, linear within a bucket."""
    buckets = sorted(buckets)
    total = buckets[-1][1]
    if total == 0:
        return 0.0
    rank = q * total
    lower_bound, lower_count = 0.0, 0.0
    for bound, count in buckets:
        if count >= rank:
            if bound == float("inf"):
                return lower_bound
            return lower_bound + (bound - lower_bound) * (rank - lower_count) / max(count - lower_count, 1e-9)
        lower_bound, lower_count = bound, count
    return buckets[-1][0]


def attribute(before: dict, after: dict) -> list[dict]:
    histograms: dict[tuple[str, str], list[tuple[float, float]]] = defaultdict(list)
    sums: dict[tuple[str, str], float] = {}
    counts: dict[tuple[str, str], float] = {}
    maxima: dict[tuple[str, str], float] = {}
    for (name, labels), value in after.items():
        delta = value - before.get((name, labels), 0.0)
        if name.endswith("_bucket"):
            le = re.search(r'le="([^"]+)"', labels).group(1)
            bound = float("inf") if le == "+Inf" else float(le)
            histograms[(name[: -len("_bucket")], labels_without_le(labels))].append((bound, delta))
        elif name.endswith("_sum"):
            sums[(name[: -len("_sum")], labels)] = delta
        elif name.endswith("_count"):
            counts[(name[: -len("_count")], labels)] = delta
        elif name.endswith("_max"):
            maxima[(name[: -len("_max")], labels)] = value
        elif name.endswith("_total"):
            counts[(name, labels)] = delta
    rows = []
    for key, count in sorted(counts.items()):
        if count <= 0:
            continue
        row = {"metric": key[0], "labels": key[1], "count": int(count),
               "mean_ms": 1000 * sums.get(key, 0.0) / count, "max_ms": 1000 * maxima.get(key, 0.0)}  # fmt: skip
        if key in histograms:
            for q in (0.5, 0.95, 0.99):
                row[f"p{int(q * 100)}_ms"] = 1000 * quantile(histograms[key], q)
        rows.append(row)
    return rows


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--rate", type=int, default=40)
    parser.add_argument("--duration", default="30s")
    parser.add_argument("--tours", type=int, default=400)
    parser.add_argument("--users", type=int, default=200)
    parser.add_argument("--fresh", action="store_true",
                        help="restart tour-core first (its warm-up runs) and empty the tour cache: a new instance")
    parser.add_argument("--tag", default="attribute-cold", help="name of the raw output in benchmarks/out")
    args = parser.parse_args()

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    check_stack()
    rows = tours_psql(f"SELECT setseed(0.42); SELECT id FROM tours ORDER BY random() LIMIT {args.tours}").splitlines()
    ids = [int(line) for line in rows if line.strip().isdigit()]
    (OUT_DIR / "integrations-ids.json").write_text(json.dumps(ids))
    secret = env_file_value("JWT_SECRET") or _secret_from_env_file()
    tokens = [mint(secret, f"integrations-user-{i}", ["CUSTOMER"], 7200) for i in range(args.users)]
    (OUT_DIR / "integrations-tokens.json").write_text(json.dumps(tokens))
    day = datetime.now(ZoneInfo("Africa/Windhoek")).date() + timedelta(days=5)

    if args.fresh:
        sh("docker", "compose", "restart", "tour-core-service")
        sh("docker", "compose", "up", "-d", "--wait", "--no-recreate", "tour-core-service")
        script = "redis-cli --scan --pattern 'tour::*' | xargs -r redis-cli del > /dev/null"
        sh("docker", "compose", "exec", "-T", "redis-cache", "sh", "-c", script)
    memory = vm_memory()
    prepare("cold")
    lookups_before = weather_lookups()
    before = scrape()
    summary = run_k6(compose_network(), args.rate, args.duration, day, args.tag)
    after = scrape()
    lookups_after = weather_lookups()

    k6 = {stat: metric(summary, "http_req_duration", stat) for stat in ("med", "p(95)", "p(99)", "max")}
    result = {
        "vmMemoryBefore": memory,
        "vmMemoryAfter": vm_memory(),
        "requests": metric(summary, "http_reqs", "count"),
        "errors": metric(summary, "detail_errors", "count"),
        "k6": k6,
        "weather": {k: lookups_after.get(k, 0) - lookups_before.get(k, 0) for k in lookups_after},
        "stages": attribute(before, after),
    }
    (OUT_DIR / f"{args.tag}-result.json").write_text(json.dumps(result, indent=2))

    print(f"Docker VM: {memory} before, {result['vmMemoryAfter']} after")
    print(f"k6 through the gateway: {int(result['requests'])} requests, {int(result['errors'])} errors, "
          f"p50 {k6['med']:.1f}  p95 {k6['p(95)']:.1f}  p99 {k6['p(99)']:.1f}  max {k6['max']:.1f} ms")  # fmt: skip
    print(f"weather outcomes: {result['weather']}")
    print("| Metric | Labels | Count | Mean (ms) | p50 (ms) | p95 (ms) | p99 (ms) | Max (ms) |")
    for row in result["stages"]:
        print(f"| {row['metric']} | {row['labels']} | {row['count']} | {row['mean_ms']:.1f} | "
              f"{row.get('p50_ms', float('nan')):.1f} | {row.get('p95_ms', float('nan')):.1f} | "
              f"{row.get('p99_ms', float('nan')):.1f} | {row['max_ms']:.1f} |")  # fmt: skip


if __name__ == "__main__":
    main()
