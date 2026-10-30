#!/usr/bin/env python3
"""Load-test catalogue search through the gateway, cache-cold then cache-warm, plus a rate-limit burst.

  1. check the catalogue holds the 100k seed (scripts/seed_tours.sql; --seed appends it);
  2. mint one JWT per simulated user (scripts/dev_token.py);
  3. cold: empty the cache (redis-cache), run benchmarks/k6/search.js (SCENARIO=mix) at --rate requests per second;
  4. warm: run the same mix again straight away, with the cache as the cold run left it;
  5. burst: one user fires --burst-rate requests per second at search, to show the limiter's 429s.
The cache hit ratio of each run is the difference in tour-core's cache_gets_total counters
(/actuator/prometheus, read inside the container: it is not routed through the gateway).

Writes the results between the k6 markers in docs/benchmarks/search.md; the rest of that file
(EXPLAIN plans) is left alone. Needs the stack running (docker compose up -d --wait). Standard library only.

    python benchmarks/run_search_benchmark.py
    python benchmarks/run_search_benchmark.py --seed --rate 150 --duration 90s
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "benchmarks"))
from dev_token import _secret_from_env_file, mint  # noqa: E402
from run_booking_contention import K6_IMAGE, OUT_DIR, compose_network, env_file_value, machine, sh  # noqa: E402

REPORT = ROOT / "docs" / "benchmarks" / "search.md"
START, END = "<!-- k6:start -->", "<!-- k6:end -->"
SEED_SIZE = 100_000


def tours_psql(query: str) -> str:
    user = env_file_value("TOURS_DB_USER") or "tours"
    return sh("docker", "compose", "exec", "-T", "tours-db", "psql", "-U", user, "-d", "tours", "-v",
              "ON_ERROR_STOP=1", "-tAc", query)  # fmt: skip


def seed() -> None:
    user = env_file_value("TOURS_DB_USER") or "tours"
    print("seeding 100k tours (appended: existing tours are kept)...")
    with open(ROOT / "scripts" / "seed_tours.sql", "rb") as script:
        result = subprocess.run(
            ["docker", "compose", "exec", "-T", "tours-db", "psql", "-U", user, "-d", "tours",
             "-v", "ON_ERROR_STOP=1"],
            cwd=ROOT, stdin=script, capture_output=True, text=True,
        )  # fmt: skip
    if result.returncode != 0:
        raise SystemExit(f"seed failed:\n{result.stdout}\n{result.stderr}")
    print(result.stdout.strip().splitlines()[-1])


def cache_counters() -> dict[tuple[str, str], float]:
    """cache_gets_total by (cache, result) from tour-core's Prometheus endpoint."""
    text = sh("docker", "compose", "exec", "-T", "tour-core-service", "wget", "-qO-",
              "http://localhost:8081/actuator/prometheus")  # fmt: skip
    counters: dict[tuple[str, str], float] = {}
    for line in text.splitlines():
        if not line.startswith("cache_gets_total{"):
            continue
        labels = dict(re.findall(r'(\w+)="([^"]*)"', line))
        key = (labels.get("cache", "?"), labels.get("result", "?"))
        counters[key] = counters.get(key, 0.0) + float(line.rsplit(" ", 1)[1])
    return counters


def hit_ratio(before: dict, after: dict, cache: str) -> tuple[int, int]:
    hits = after.get((cache, "hit"), 0) - before.get((cache, "hit"), 0)
    misses = after.get((cache, "miss"), 0) - before.get((cache, "miss"), 0)
    return int(hits), int(misses)


def run_k6(network: str, scenario: str, rate: int, duration: str, tokens: str, tag: str) -> dict:
    summary = OUT_DIR / f"{tag}.json"
    summary.unlink(missing_ok=True)
    result = subprocess.run(
        [
            "docker", "run", "--rm", "--network", network,
            "-v", f"{ROOT / 'benchmarks' / 'k6'}:/scripts:ro",
            "-v", f"{OUT_DIR}:/out",
            K6_IMAGE, "run", "--quiet",
            "-e", "BASE_URL=http://api-gateway:8080",
            "-e", f"TOKENS=/out/{tokens}",
            "-e", f"SCENARIO={scenario}",
            "-e", f"RATE={rate}",
            "-e", f"DURATION={duration}",
            "-e", f"SUMMARY=/out/{tag}.json",
            "/scripts/search.js",
        ],
        cwd=ROOT, capture_output=True, text=True,
    )  # fmt: skip
    if not summary.exists():
        raise SystemExit(f"k6 produced no summary ({result.returncode}):\n{result.stdout}\n{result.stderr}")
    if result.returncode != 0:
        print(f"k6 exited {result.returncode} (a threshold failed)\n{result.stderr[-2000:]}")
    return json.loads(summary.read_text())


def metric(summary: dict, name: str, stat: str) -> float:
    values = summary["metrics"].get(name, {}).get("values", {})
    return float(values.get(stat, 0))


def row(label: str, s: dict, hits_misses: tuple[int, int]) -> str:
    hits, misses = hits_misses
    ratio = f"{100 * hits / (hits + misses):.1f}%" if hits + misses else "n/a"
    duration = "http_req_duration"
    return (
        f"| {label} | {metric(s, duration, 'med'):.1f} | {metric(s, duration, 'p(95)'):.1f} | "
        f"{metric(s, duration, 'p(99)'):.1f} | {metric(s, 'first_page_duration', 'p(95)'):.1f} | "
        f"{metric(s, 'later_page_duration', 'p(95)'):.1f} | {metric(s, 'http_reqs', 'rate'):.0f} | "
        f"{int(metric(s, 'search_ok', 'count'))} | {int(metric(s, 'search_errors', 'count'))} | "
        f"{int(metric(s, 'dropped_iterations', 'count'))} | {ratio} ({hits} / {hits + misses}) |"
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--seed", action="store_true", help="append scripts/seed_tours.sql's 100k tours first")
    parser.add_argument("--rate", type=int, default=100, help="user actions per second in the mix")
    parser.add_argument("--duration", default="60s")
    parser.add_argument("--users", type=int, default=200, help="distinct signed-in users (JWTs)")
    parser.add_argument("--burst-rate", type=int, default=100)
    parser.add_argument("--burst-duration", default="10s")
    args = parser.parse_args()

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    if args.seed:
        seed()
    count = int(tours_psql("SELECT count(*) FROM tours"))
    if count < SEED_SIZE:
        raise SystemExit(f"only {count} tours; run with --seed to load the {SEED_SIZE:,} tour catalogue")
    departures = int(tours_psql("SELECT count(*) FROM tour_departure_summary"))

    secret = env_file_value("JWT_SECRET") or _secret_from_env_file()
    if not secret:
        raise SystemExit("JWT_SECRET not found in .env")
    (OUT_DIR / "search-tokens.json").write_text(
        json.dumps([mint(secret, f"search-user-{i}", ["CUSTOMER"], 3600) for i in range(args.users)])
    )
    (OUT_DIR / "burst-token.json").write_text(json.dumps([mint(secret, f"burst-{int(time.time())}", [], 3600)]))
    network = compose_network()
    host = machine()

    print("--- cold: empty cache")
    sh("docker", "compose", "exec", "-T", "redis-cache", "redis-cli", "FLUSHALL")
    before = cache_counters()
    cold = run_k6(network, "mix", args.rate, args.duration, "search-tokens.json", "search-cold")
    cold_counts = cache_counters()

    print("--- warm: same mix, cache as the cold run left it")
    warm = run_k6(network, "mix", args.rate, args.duration, "search-tokens.json", "search-warm")
    warm_counts = cache_counters()

    print("--- burst: one user against the search budget")
    burst = run_k6(network, "burst", args.burst_rate, args.burst_duration, "burst-token.json", "search-burst")

    header = [
        "| Run | p50 (ms) | p95 (ms) | p99 (ms) | first page p95 | later pages p95 | Throughput (req/s) | 200 | Errors |"
        " Dropped | Search cache hits |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    burst_ok, burst_429 = int(metric(burst, "search_ok", "count")), int(metric(burst, "search_limited", "count"))
    lines = [
        START,
        f"Generated by `benchmarks/run_search_benchmark.py` on {datetime.now(UTC):%Y-%m-%d %H:%M} UTC.",
        "",
        f"Catalogue: {count:,} tours, {departures:,} departures in the read model. Load: {args.rate} user actions/s "
        f"(open model, k6 `constant-arrival-rate`) for {args.duration}, spread over {args.users} signed-in users, "
        "through the gateway. A paging action makes up to 4 requests, so requests/s is above actions/s.",
        "",
        *header,
        row("cold (cache emptied first)", cold, hit_ratio(before, cold_counts, "search")),
        row("warm (straight after)", warm, hit_ratio(cold_counts, warm_counts, "search")),
        "",
        f"`tour:{{id}}` cache during the two runs: {hit_ratio(before, warm_counts, 'tour')[0]} hits, "
        f"{hit_ratio(before, warm_counts, 'tour')[1]} misses (search does not read it; listed for completeness).",
        "",
        f"Rate-limit burst: one user, {args.burst_rate} searches/s for {args.burst_duration} against a budget of "
        f"{env_file_value('RATE_LIMIT_SEARCH_RPS') or 20}/s with a burst of {env_file_value('RATE_LIMIT_SEARCH_BURST') or 40}: "
        f"**{burst_ok} answered 200, {burst_429} refused with 429** "
        f"(p95 of the 429s and 200s together: {metric(burst, 'http_req_duration', 'p(95)'):.1f} ms).",
        "",
        "| | |",
        "| --- | --- |",
        f"| Host CPU | {host['host_cpu']} ({host['host_cores']} logical cores) |",
        f"| Host RAM | {host['host_ram_gb']} GB |",
        f"| Host OS | {host['host_os']} |",
        f"| Docker | {host['docker']} |",
        f"| Docker VM | {host['docker_vm']} (shared by every container and k6) |",
        f"| k6 | `{K6_IMAGE}` |",
        "",
        "Raw k6 summaries: `benchmarks/out/search-*.json` (not committed).",
        END,
    ]
    report = REPORT.read_text(encoding="utf-8") if REPORT.exists() else f"# Search\n\n{START}\n{END}\n"
    if START not in report:
        report += f"\n{START}\n{END}\n"
    head, rest = report.split(START, 1)
    tail = rest.split(END, 1)[1]
    REPORT.write_text(head + "\n".join(lines) + tail, encoding="utf-8", newline="\n")
    print("\n".join(lines))
    print(f"wrote {REPORT.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
