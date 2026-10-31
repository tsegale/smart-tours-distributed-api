#!/usr/bin/env python3
"""Load-test tour detail with a display currency and departure weather while the providers misbehave.

Needs the stack running with the chaos profile, so tour-core calls the WireMock provider stubs:

    docker compose --profile chaos --env-file .env --env-file infra/chaos/chaos.env up -d --wait
    python benchmarks/run_integrations_benchmark.py

Each scenario is k6 (benchmarks/k6/integrations.js) on GET /api/tours/{id}?currency=EUR&include=weather&date=D
through the gateway, at a constant arrival rate, over tours spread across the 100k seed (many weather cells):

  warm          healthy stubs, every tour's weather already cached
  cold          healthy stubs, weather cache emptied first
  slow-before   forecast stub answers after 2 s, cache emptied, circuit breaker disabled (it can't open):
                what every request costs while a slow provider is still being called
  slow-after    the same provider, breaker enabled: it opens within the first second
  dead          every stub resets the connection, cache emptied, breaker enabled

Exchange rates are refreshed daily and read from memory, so the Frankfurter stub's state doesn't touch this
path; in the dead scenario it is dead too, to show exactly that.

Per run it records k6's latency percentiles and error count, how tour-core answered weather (the change in
weather_lookups_total by outcome) and the breaker's state afterwards. Each scenario runs --rounds times;
the report shows the median of each figure and the range. Writes docs/benchmarks/integrations.md between
the k6 markers. Standard library only.
"""

from __future__ import annotations

import argparse
import json
import re
import statistics
import subprocess
import sys
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "benchmarks"))
from dev_token import _secret_from_env_file, mint  # noqa: E402
from run_booking_contention import K6_IMAGE, OUT_DIR, compose_network, env_file_value, machine, sh  # noqa: E402

REPORT = ROOT / "docs" / "benchmarks" / "integrations.md"
START, END = "<!-- k6:start -->", "<!-- k6:end -->"
GATEWAY = "http://127.0.0.1:8080"
STUBS_ADMIN = "http://127.0.0.1:8090/__admin"
FORECAST_BREAKER = "open-meteo-forecast"
SCENARIOS = ["warm", "cold", "slow-before", "slow-after", "dead"]


# ------------------------------------------------------------------ the system under test


def tours_psql(query: str) -> str:
    user = env_file_value("TOURS_DB_USER") or "tours"
    return sh("docker", "compose", "exec", "-T", "tours-db", "psql", "-U", user, "-d", "tours", "-v",
              "ON_ERROR_STOP=1", "-tAc", query)  # fmt: skip


def core(*command: str) -> str:
    return sh("docker", "compose", "exec", "-T", "tour-core-service", *command)


def stubs(provider: str, state: str) -> None:
    """Scenario state of the provider stubs: Started (healthy), slow, errors or dead."""
    for scenario in {"fx": ["fx"], "weather": ["weather"], "all": ["fx", "weather"]}[provider]:
        request = urllib.request.Request(f"{STUBS_ADMIN}/scenarios/{scenario}/state", method="PUT",
                                         data=json.dumps({"state": state}).encode(),
                                         headers={"Content-Type": "application/json"})  # fmt: skip
        urllib.request.urlopen(request, timeout=5).close()


def breaker(update: str | None = None) -> str:
    """State of the forecast breaker, after an optional CLOSE / DISABLE / FORCE_OPEN through the actuator."""
    url = f"http://localhost:8081/actuator/circuitbreakers/{FORECAST_BREAKER}"
    if update:
        core("wget", "-qO-", "--header", "Content-Type: application/json",
             "--post-data", json.dumps({"updateState": update}), url)  # fmt: skip
    states = json.loads(core("wget", "-qO-", "http://localhost:8081/actuator/circuitbreakers"))
    return states["circuitBreakers"][FORECAST_BREAKER]["state"]


def empty_weather_cache() -> int:
    script = "redis-cli --scan --pattern 'weather:*' | xargs -r redis-cli del | awk '{s+=$1} END {print s+0}'"
    return int(sh("docker", "compose", "exec", "-T", "redis-cache", "sh", "-c", script) or 0)


def weather_lookups() -> dict[str, float]:
    """weather_lookups_total by outcome (forecast and climate normal together)."""
    text = core("wget", "-qO-", "http://localhost:8081/actuator/prometheus")
    totals: dict[str, float] = {}
    for line in text.splitlines():
        if line.startswith("weather_lookups_total{"):
            outcome = re.search(r'outcome="([^"]+)"', line).group(1)
            totals[outcome] = totals.get(outcome, 0.0) + float(line.rsplit(" ", 1)[1])
    return totals


def check_stack() -> None:
    url = core("printenv", "OPEN_METEO_FORECAST_BASE_URL")
    if "provider-stubs" not in url:
        raise SystemExit(f"tour-core calls {url}: start the stack with the chaos profile and infra/chaos/chaos.env")
    try:
        urllib.request.urlopen(f"{STUBS_ADMIN}/health", timeout=5).close()
    except OSError as ex:
        raise SystemExit(f"provider stubs unreachable ({ex})") from ex


def detail(tour_id: int, token: str, day: date) -> dict:
    request = urllib.request.Request(f"{GATEWAY}/api/tours/{tour_id}?currency=EUR&include=weather&date={day}",
                                     headers={"Authorization": f"Bearer {token}"})  # fmt: skip
    with urllib.request.urlopen(request, timeout=30) as response:
        return json.loads(response.read())


# ------------------------------------------------------------------ k6


def run_k6(network: str, rate: int, duration: str, day: date, tag: str) -> dict:
    summary = OUT_DIR / f"{tag}.json"
    summary.unlink(missing_ok=True)
    result = subprocess.run(
        [
            "docker", "run", "--rm", "--network", network,
            "-v", f"{ROOT / 'benchmarks' / 'k6'}:/scripts:ro",
            "-v", f"{OUT_DIR}:/out",
            K6_IMAGE, "run", "--quiet",
            "-e", "BASE_URL=http://api-gateway:8080",
            "-e", "TOKENS=/out/integrations-tokens.json",
            "-e", "IDS=/out/integrations-ids.json",
            "-e", f"DATE={day}",
            "-e", f"RATE={rate}",
            "-e", f"DURATION={duration}",
            "-e", f"SUMMARY=/out/{tag}.json",
            "/scripts/integrations.js",
        ],
        cwd=ROOT, capture_output=True, text=True, check=False,
    )  # fmt: skip
    if not summary.exists():
        raise SystemExit(f"k6 produced no summary ({result.returncode}):\n{result.stdout}\n{result.stderr}")
    return json.loads(summary.read_text())


def metric(summary: dict, name: str, stat: str) -> float:
    return float(summary["metrics"].get(name, {}).get("values", {}).get(stat, 0))


# ------------------------------------------------------------------ scenarios


def prepare(scenario: str) -> None:
    """Providers, breaker and cache as the scenario needs them."""
    if scenario == "warm":
        stubs("all", "Started")
        breaker("CLOSE")
        return
    stubs("all", "Started")
    breaker("CLOSE")
    empty_weather_cache()
    if scenario.startswith("slow"):
        stubs("weather", "slow")
        if scenario == "slow-before":
            breaker("DISABLE")
    elif scenario == "dead":
        stubs("all", "dead")


def measure(scenario: str, network: str, args: argparse.Namespace, day: date, round_no: int) -> dict:
    prepare(scenario)
    before = weather_lookups()
    summary = run_k6(network, args.rate, args.duration, day, f"integrations-{scenario}-r{round_no}")
    after = weather_lookups()
    outcomes = {k: int(after.get(k, 0) - before.get(k, 0)) for k in ("cached", "fetched", "stale", "unavailable")}
    requests = metric(summary, "http_reqs", "count")
    result = {
        "scenario": scenario,
        "p50": metric(summary, "http_req_duration", "med"),
        "p95": metric(summary, "http_req_duration", "p(95)"),
        "p99": metric(summary, "http_req_duration", "p(99)"),
        "max": metric(summary, "http_req_duration", "max"),
        "requests": requests,
        "rps": metric(summary, "http_reqs", "rate"),
        "errors": metric(summary, "detail_errors", "count"),
        "error_rate": 100 * metric(summary, "detail_errors", "count") / requests if requests else 0.0,
        "priced": metric(summary, "display_priced", "count"),
        "dropped": metric(summary, "dropped_iterations", "count"),
        "breaker": breaker(),
        **{f"weather_{k}": v for k, v in outcomes.items()},
    }
    print(f"  {scenario:11} r{round_no}: p50 {result['p50']:7.1f}  p95 {result['p95']:7.1f}  "
          f"p99 {result['p99']:7.1f} ms  errors {int(result['errors'])}  weather {outcomes}  "
          f"breaker {result['breaker']}")  # fmt: skip
    return result


def median_of(rows: list[dict], key: str) -> float:
    return statistics.median(r[key] for r in rows)


def cell(rows: list[dict], key: str, fmt: str = "{:.1f}") -> str:
    values = [r[key] for r in rows]
    if len(values) == 1 or min(values) == max(values):
        return fmt.format(values[0])
    return f"{fmt.format(statistics.median(values))} ({fmt.format(min(values))} to {fmt.format(max(values))})"


def write_report(
    results: dict[str, list[dict]], args: argparse.Namespace, day: date, cells: int, tours: int, host: dict[str, str]
) -> None:
    lines = [
        START,
        (
            f"Generated by `benchmarks/run_integrations_benchmark.py` on {datetime.now(UTC):%Y-%m-%d}. "
            f"`GET /api/tours/{{id}}?currency=EUR&include=weather&date={day}` through the gateway, "
            f"{args.rate} requests/s for {args.duration} per run (k6 `constant-arrival-rate`), {args.rounds} rounds "
            f"per scenario, {args.users} signed-in users, {tours} tours in {cells} weather cells of the 100k seed. "
            "Each figure is the median of the rounds, with the range in brackets when the rounds differed. "
            "Providers are the WireMock stubs of the chaos profile."
        ),
        "",
        "| Scenario | p50 (ms) | p95 (ms) | p99 (ms) | Error rate | Weather ok / stale / unavailable | Breaker after |",
        "| --- | ---: | ---: | ---: | ---: | --- | --- |",
    ]
    labels = {
        "warm": "warm cache",
        "cold": "cold cache, healthy stubs",
        "slow-before": "provider slow (2 s), before the circuit opens (breaker disabled)",
        "slow-after": "provider slow (2 s), circuit allowed to open",
        "dead": "every provider dead (connection reset)",
    }
    for scenario in SCENARIOS:
        rows = results.get(scenario)
        if not rows:
            continue
        ok = int(median_of(rows, "weather_cached") + median_of(rows, "weather_fetched"))
        lines.append(
            f"| {labels[scenario]} | {cell(rows, 'p50')} | {cell(rows, 'p95')} | {cell(rows, 'p99')} | "
            f"{cell(rows, 'error_rate', '{:.2f}')}% | {ok} / {int(median_of(rows, 'weather_stale'))} / "
            f"{int(median_of(rows, 'weather_unavailable'))} | {', '.join(sorted({r['breaker'] for r in rows}))} |"
        )
    lines += [
        "",
        (
            "Weather outcomes are tour-core's own count (`weather_lookups_total`) over each run: answered from the "
            "cache or fetched in time (ok), served stale, or without weather (unavailable). Every response is checked: "
            "an error is anything but a 200 carrying the tour, its EUR display price and a weatherStatus."
        ),
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
        "Raw k6 summaries: `benchmarks/out/integrations-*.json` (not committed).",
        END,
    ]
    text = REPORT.read_text(encoding="utf-8") if REPORT.exists() else f"{START}\n{END}\n"
    head, rest = text.split(START, 1)
    tail = rest.split(END, 1)[1]
    REPORT.write_text(head + "\n".join(lines) + tail, encoding="utf-8")
    raw = OUT_DIR / "integrations-results.json"
    raw.write_text(json.dumps(results, indent=2))
    print(f"wrote {REPORT.relative_to(ROOT)} and {raw.relative_to(ROOT)}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--rate", type=int, default=40, help="requests per second")
    parser.add_argument("--duration", default="60s")
    parser.add_argument("--rounds", type=int, default=3)
    parser.add_argument("--tours", type=int, default=400, help="distinct tours requested")
    parser.add_argument("--users", type=int, default=200, help="distinct signed-in users (JWTs)")
    parser.add_argument("--warmup", default="20s")
    parser.add_argument("--scenarios", default=",".join(SCENARIOS))
    args = parser.parse_args()
    scenarios = [s for s in SCENARIOS if s in args.scenarios.split(",")]

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    check_stack()
    rows = tours_psql(f"SELECT setseed(0.42); SELECT id, round(ST_Y(location)::numeric, 1), round(ST_X(location)::numeric, 1) "
                      f"FROM tours ORDER BY random() LIMIT {args.tours}").splitlines()  # fmt: skip
    picked = [line.split("|") for line in rows if line.count("|") == 2]
    ids = [int(r[0]) for r in picked]
    cells = len({(r[1], r[2]) for r in picked})
    if len(ids) < args.tours:
        raise SystemExit(
            f"only {len(ids)} tours; load the seed first (python benchmarks/run_search_benchmark.py --seed)"
        )
    (OUT_DIR / "integrations-ids.json").write_text(json.dumps(ids))

    secret = env_file_value("JWT_SECRET") or _secret_from_env_file()
    tokens = [mint(secret, f"integrations-user-{i}", ["CUSTOMER"], 7200) for i in range(args.users)]
    (OUT_DIR / "integrations-tokens.json").write_text(json.dumps(tokens))
    day = datetime.now(ZoneInfo("Africa/Windhoek")).date() + timedelta(days=5)
    network = compose_network()

    stubs("all", "Started")
    breaker("CLOSE")
    probe = detail(ids[0], tokens[0], day)
    if not probe.get("displayPrice"):
        raise SystemExit(f"no EUR display price ({probe.get('displayPriceUnavailable')}): are rates loaded?")
    print(f"{len(ids)} tours in {cells} weather cells, date {day}; warming up for {args.warmup}")
    run_k6(network, args.rate, args.warmup, day, "integrations-warmup")

    results: dict[str, list[dict]] = {s: [] for s in scenarios}
    for round_no in range(1, args.rounds + 1):
        print(f"round {round_no}")
        for scenario in scenarios:
            if scenario == "warm":
                # Every tour's weather fetched once, so the warm run never misses
                stubs("all", "Started")
                with ThreadPoolExecutor(8) as pool:
                    list(pool.map(lambda i: detail(i, tokens[i % len(tokens)], day), ids))
            results[scenario].append(measure(scenario, network, args, day, round_no))
            time.sleep(2)
    stubs("all", "Started")
    breaker("CLOSE")
    write_report(results, args, day, cells, len(ids), machine())


if __name__ == "__main__":
    main()
