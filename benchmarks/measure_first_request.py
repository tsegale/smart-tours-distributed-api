#!/usr/bin/env python3
"""First requests after tour-core becomes ready: what the first visitors pay for a cold JVM.

Restarts tour-core-service, waits until its readiness probe answers UP (polled every 250 ms from inside the
container, as the compose healthcheck does), then immediately sends --requests sequential requests through the
gateway:

    GET /api/tours/{id}?currency=EUR&include=weather&date=<5 days ahead>

Every request is for a different tour that nothing has requested before (a fresh weather cell and a tour-cache
miss each time), so request 1 and request N do the same work and differ only in how warm the JVM is. Repeated
--restarts times; prints the latency of request i per restart and the median, and the time from restart to ready.

Works against the real providers or the chaos stubs; the output names which. Standard library only.

    python benchmarks/measure_first_request.py --label before
"""

from __future__ import annotations

import argparse
import json
import random
import statistics
import sys
import time
import urllib.request
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "benchmarks"))
from dev_token import _secret_from_env_file, mint  # noqa: E402
from run_booking_contention import OUT_DIR, env_file_value, sh  # noqa: E402
from run_integrations_benchmark import GATEWAY, core, tours_psql  # noqa: E402


def ready() -> bool:
    probe = sh("docker", "compose", "exec", "-T", "tour-core-service", "wget", "-qO-",
               "http://localhost:8081/actuator/health/readiness", check=False)  # fmt: skip
    return '"UP"' in probe


def restart_and_wait(timeout: float = 300) -> float:
    started = time.monotonic()
    sh("docker", "compose", "restart", "tour-core-service")
    while time.monotonic() - started < timeout:
        if ready():
            return time.monotonic() - started
        time.sleep(0.25)
    raise SystemExit("tour-core did not become ready")


def timed_detail(tour_id: int, token: str, day: str) -> tuple[float, str]:
    request = urllib.request.Request(f"{GATEWAY}/api/tours/{tour_id}?currency=EUR&include=weather&date={day}",
                                     headers={"Authorization": f"Bearer {token}"})  # fmt: skip
    started = time.perf_counter()
    with urllib.request.urlopen(request, timeout=60) as response:
        body = json.loads(response.read())
    return (time.perf_counter() - started) * 1000, body.get("weatherStatus", "?")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--label", required=True, help="name of this measurement, e.g. before or after")
    parser.add_argument("--restarts", type=int, default=3)
    parser.add_argument("--requests", type=int, default=5)
    args = parser.parse_args()

    providers = core("printenv", "OPEN_METEO_FORECAST_BASE_URL").strip()
    rows = tours_psql("SELECT id, round(ST_Y(location)::numeric, 1), round(ST_X(location)::numeric, 1) FROM tours")
    by_cell: dict[str, int] = {}
    for line in rows.splitlines():
        if line.count("|") == 2:
            tour_id, lat, lon = line.split("|")
            by_cell.setdefault(f"{lat},{lon}", int(tour_id))
    fresh = list(by_cell.values())
    random.shuffle(fresh)
    needed = args.restarts * args.requests
    if len(fresh) < needed:
        raise SystemExit(f"only {len(fresh)} weather cells, need {needed}")

    token = mint(env_file_value("JWT_SECRET") or _secret_from_env_file(), "first-request", ["CUSTOMER"], 3600)
    day = str(datetime.now(ZoneInfo("Africa/Windhoek")).date() + timedelta(days=5))
    latencies: list[list[float]] = [[] for _ in range(args.requests)]
    statuses: list[list[str]] = [[] for _ in range(args.requests)]
    to_ready: list[float] = []
    for restart in range(args.restarts):
        to_ready.append(restart_and_wait())
        for i in range(args.requests):
            ms, status = timed_detail(fresh.pop(), token, day)
            latencies[i].append(ms)
            statuses[i].append(status)
        print(f"restart {restart + 1}: ready after {to_ready[-1]:.1f} s; "
              + ", ".join(f"{ms[-1]:.0f} ms ({st[-1]})" for ms, st in zip(latencies, statuses)))  # fmt: skip

    print(f"\n{args.label}: providers {providers}, {args.restarts} restarts")
    print(f"restart to ready: median {statistics.median(to_ready):.1f} s")
    print("| Request | " + " | ".join(f"restart {r + 1}" for r in range(args.restarts)) + " | median | weather |")
    for i, values in enumerate(latencies):
        cells = " | ".join(f"{v:.0f}" for v in values)
        print(f"| {i + 1} | {cells} | {statistics.median(values):.0f} | {', '.join(statuses[i])} |")
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    (OUT_DIR / f"first-request-{args.label}.json").write_text(
        json.dumps({"providers": providers, "toReady": to_ready, "latenciesMs": latencies, "weather": statuses}))


if __name__ == "__main__":
    main()
