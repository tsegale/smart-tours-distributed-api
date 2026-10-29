#!/usr/bin/env python3
"""Benchmark the three seat reservation strategies under contention and write the results.

For each RESERVATION_STRATEGY (pessimistic, optimistic, atomic):
  1. restart booking-service with that strategy and PAYMENT_PROVIDER=fake;
  2. open a fresh departure with --seats seats (plus a warm-up departure, not measured);
  3. run benchmarks/k6/booking_contention.js in the grafana/k6 container on the compose
     network, --vus virtual users sharing --iterations booking attempts, through the gateway;
  4. read the final seat counts back from the API and straight from bookings-db, and check
     that inventory and bookings agree and nothing was oversold.
Then it writes docs/benchmarks/booking-locking.md with the machine, the settings and the table.

Needs the stack running (docker compose up -d --wait) with STRIPE_WEBHOOK_SECRET set.
Standard library only.

    python benchmarks/run_booking_contention.py
    python benchmarks/run_booking_contention.py --vus 200 --iterations 2000
"""
from __future__ import annotations

import argparse
import json
import os
import platform
import statistics
import subprocess
import sys
import time
import urllib.error
import urllib.request
from datetime import UTC, datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
from dev_token import _secret_from_env_file, mint  # noqa: E402

K6_IMAGE = "grafana/k6:2.3.0"
STRATEGIES = ("pessimistic", "optimistic", "atomic")
GATEWAY = "http://127.0.0.1:8080"
OUT_DIR = ROOT / "benchmarks" / "out"
REPORT = ROOT / "docs" / "benchmarks" / "booking-locking.md"


def sh(*args: str, env: dict[str, str] | None = None, check: bool = True) -> str:
    result = subprocess.run(args, cwd=ROOT, env=env, capture_output=True, text=True)
    if check and result.returncode != 0:
        raise SystemExit(f"command failed ({result.returncode}): {' '.join(args)}\n{result.stdout}\n{result.stderr}")
    return result.stdout.strip()


def api(method: str, path: str, token: str, body: dict | None = None) -> dict | list:
    request = urllib.request.Request(
        GATEWAY + path,
        data=json.dumps(body).encode() if body is not None else None,
        method=method,
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return json.loads(response.read())
    except urllib.error.HTTPError as exc:
        raise SystemExit(f"{method} {path} -> {exc.code}: {exc.read().decode()}") from exc


def psql(query: str) -> str:
    user = os.environ.get("BOOKINGS_DB_USER") or env_file_value("BOOKINGS_DB_USER") or "bookings"
    return sh("docker", "compose", "exec", "-T", "bookings-db", "psql", "-U", user, "-d", "bookings", "-tAc", query)


def env_file_value(key: str) -> str | None:
    env_file = ROOT / ".env"
    if env_file.exists():
        for line in env_file.read_text().splitlines():
            if line.startswith(f"{key}="):
                return line.split("=", 1)[1].strip()
    return None


def restart_booking_service(strategy: str | None) -> None:
    """Recreate booking-service with `strategy`, or with the compose default when None."""
    env = {**os.environ, "PAYMENT_PROVIDER": "fake"}
    env.pop("RESERVATION_STRATEGY", None)
    if strategy is not None:
        env["RESERVATION_STRATEGY"] = strategy
    sh("docker", "compose", "up", "-d", "--wait", "--no-deps", "booking-service", env=env)
    if strategy is None:
        return
    actual = sh("docker", "compose", "exec", "-T", "booking-service", "printenv", "RESERVATION_STRATEGY")
    if actual != strategy:
        raise SystemExit(f"booking-service runs {actual!r}, expected {strategy!r}")


def compose_network() -> str:
    container = sh("docker", "compose", "ps", "-q", "api-gateway")
    networks = json.loads(sh("docker", "inspect", "-f", "{{json .NetworkSettings.Networks}}", container))
    return next(iter(networks))


def run_k6(network: str, availability_id: int, vus: int, iterations: int, tag: str, run_id: int) -> dict:
    summary = OUT_DIR / f"{tag}.json"
    summary.unlink(missing_ok=True)
    result = subprocess.run(
        [
            "docker", "run", "--rm", "--network", network,
            "-v", f"{ROOT / 'benchmarks' / 'k6'}:/scripts:ro",
            "-v", f"{OUT_DIR}:/out",
            K6_IMAGE, "run", "--quiet",
            "-e", "BASE_URL=http://api-gateway:8080",
            "-e", f"AVAILABILITY_ID={availability_id}",
            "-e", "TOKENS=/out/tokens.json",
            "-e", f"VUS={vus}",
            "-e", f"ITERATIONS={iterations}",
            "-e", f"SUMMARY=/out/{tag}.json",
            "-e", f"RUN_TAG={tag}-{run_id}",  # unique Idempotency-Keys per run
            "/scripts/booking_contention.js",
        ],
        cwd=ROOT, capture_output=True, text=True,
    )  # fmt: skip
    print(result.stdout.strip())
    if not summary.exists():
        raise SystemExit(f"k6 produced no summary ({result.returncode}):\n{result.stderr[-3000:]}")
    if result.returncode != 0:
        print(f"k6 exited {result.returncode} (a threshold failed: see errors column)\n{result.stderr[-1500:]}")
    return json.loads(summary.read_text())


def values(summary: dict, name: str) -> dict:
    return summary["metrics"].get(name, {}).get("values", {})


def final_state(availability_id: int) -> dict:
    row = psql(
        "SELECT a.total_slots, a.booked_slots, a.version, "
        "COALESCE(SUM(b.slots) FILTER (WHERE b.status IN ('PENDING_PAYMENT','CONFIRMED')), 0), COUNT(b.id) "
        f"FROM tour_availability a LEFT JOIN bookings b ON b.availability_id = a.id WHERE a.id = {availability_id} "
        "GROUP BY a.id"
    )
    total, booked, version, held, rows = (int(x) for x in row.split("|"))
    return {"total": total, "booked": booked, "version": version, "seats_in_bookings": held, "booking_rows": rows}


def machine() -> dict[str, str]:
    cpu = platform.processor()
    if platform.system() == "Windows":
        cpu = sh("powershell", "-NoProfile", "-Command", "(Get-CimInstance Win32_Processor).Name", check=False) or cpu
        mem = sh(
            "powershell", "-NoProfile", "-Command",
            "[math]::Round((Get-CimInstance Win32_ComputerSystem).TotalPhysicalMemory / 1GB)", check=False,
        )  # fmt: skip
    else:
        mem = ""
    info = json.loads(sh("docker", "info", "--format", "{{json .}}"))
    return {
        "host_cpu": cpu.strip(),
        "host_cores": str(os.cpu_count()),
        "host_ram_gb": mem,
        "host_os": f"{platform.system()} {platform.release()} ({platform.version()})",
        "docker": f"{info.get('ServerVersion')} ({info.get('OperatingSystem')})",
        "docker_vm": f"{info.get('NCPU')} vCPU, {int(info.get('MemTotal', 0)) / 2**30:.1f} GiB",
    }


def ms(v: float | None) -> str:
    return "n/a" if v is None else f"{v:.0f}" if v >= 10 else f"{v:.1f}"


def row(r: dict) -> dict[str, float]:
    s, f = r["summary"], r["final"]
    d, c = values(s, "http_req_duration"), values(s, "booking_created_duration")
    return {
        "p50": d.get("med"),
        "p95": d.get("p(95)"),
        "p99": d.get("p(99)"),
        "created_p95": c.get("p(95)"),
        "rps": values(s, "http_reqs").get("rate", 0),
        "created": int(values(s, "booking_created").get("count", 0)),
        "sold_out": int(values(s, "booking_sold_out").get("count", 0)),
        "busy": int(values(s, "booking_busy").get("count", 0)),
        "errors": int(values(s, "booking_errors").get("count", 0)),
        "booked": f["booked"],
        "total": f["total"],
        "agree": f["booked"] == f["seats_in_bookings"] and f["booked"] <= f["total"],
    }


def table_line(label: str, m: dict) -> str:
    return (
        f"| {label} | {ms(m['p50'])} | {ms(m['p95'])} | {ms(m['p99'])} | {ms(m['created_p95'])} | {m['rps']:.0f} "
        f"| {m['created']} | {m['sold_out']} | {m['busy']} | {m['errors']} | {m['booked']} / {m['total']} "
        f"| {'yes' if m['agree'] else '**NO**'} |"
    )


HEADER = [
    "| Strategy | p50 | p95 | p99 | 201 p95 | Throughput (req/s) | 201 | 409 sold out | 409 busy | Errors "
    "| Final booked / total | Bookings agree |",
    "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | :---: |",
]


def median_row(rows: list[dict]) -> dict:
    out: dict = {}
    for key in rows[0]:
        if key == "agree":
            out[key] = all(r[key] for r in rows)
        elif key in ("busy", "errors"):
            out[key] = sum(r[key] for r in rows)  # totals: any failure must show
        else:
            out[key] = statistics.median(r[key] for r in rows)
    for key in ("created", "sold_out", "booked", "total"):
        out[key] = int(out[key])
    return out


def write_report(results: list[dict], args: argparse.Namespace, host: dict[str, str], service: dict[str, str]) -> None:
    runs = args.runs
    lines = [
        "# Seat reservation under contention: pessimistic vs optimistic vs atomic",
        "",
        f"Generated by `benchmarks/run_booking_contention.py --runs {runs}` on {datetime.now(UTC):%Y-%m-%d %H:%M} UTC.",
        f"Measured, not estimated: {runs} run(s) per strategy, strategy order rotated each round. Re-run the",
        "script to reproduce.",
        "",
        "## Scenario",
        "",
        f"- One departure with **{args.seats} seats**. **{args.vus} virtual users** share **{args.iterations} "
        "booking attempts** (k6 `shared-iterations`), 1 seat each, so it sells out half way through.",
        "- Every request goes client -> api-gateway (JWT check) -> booking-service -> PostgreSQL, on the compose",
        "  network. One JWT per VU from `scripts/dev_token.py`, a unique `Idempotency-Key` per attempt.",
        "- `PAYMENT_PROVIDER=fake`: the hold and the payment row are written, but no network call to a PSP is made.",
        f"- Before each measured run, a warm-up run of {args.warmup} attempts on a separate departure (not reported).",
        f"- booking-service as in `docker-compose.yml`: {service['workers']} uvicorn workers, SQLAlchemy pool "
        f"{service['pool']} + {service['overflow']} overflow per worker,",
        "  `lock_timeout` 5 s, optimistic strategy 8 attempts with full-jitter backoff (5 ms base, 100 ms cap).",
        "",
        "## Machine",
        "",
        "| | |",
        "| --- | --- |",
        f"| Host CPU | {host['host_cpu']} ({host['host_cores']} logical cores) |",
        f"| Host RAM | {host['host_ram_gb']} GB |" if host["host_ram_gb"] else "| Host RAM | n/a |",
        f"| Host OS | {host['host_os']} |",
        f"| Docker | {host['docker']} |",
        f"| Docker VM | {host['docker_vm']} (shared by all 11 containers and k6) |",
        f"| k6 | `{K6_IMAGE}` |",
        "",
        "## Results",
        "",
        "Latency is k6's `http_req_duration` in milliseconds over all requests. *201 p95* covers only the",
        "requests that got a seat. Sold-out answers are cheap for all three strategies.",
        "",
    ]
    by_strategy: dict[str, list[dict]] = {}
    for r in results:
        by_strategy.setdefault(r["strategy"], []).append(row(r))
    if runs > 1:
        lines += [f"Median of {runs} runs (*409 busy* and *Errors* are totals over all runs):", ""]
    lines += HEADER
    for strategy in args.strategies:
        lines.append(table_line(strategy, median_row(by_strategy[strategy])))
    if runs > 1:
        lines += ["", "Every run, in the order they ran:", ""] + HEADER
        for r in results:
            lines.append(table_line(f"{r['strategy']} (round {r['round'] + 1})", row(r)))
    lines += [
        "",
        "*Bookings agree*: `tour_availability.booked_slots` equals the sum of seats in PENDING_PAYMENT and",
        "CONFIRMED bookings for that departure, read straight from bookings-db after the run, and is not above",
        "the total. *409 busy* is a lock wait that hit `lock_timeout`, or optimistic retries that ran out.",
        "",
        "Raw k6 summaries: `benchmarks/out/` (not committed).",
        "",
    ]
    REPORT.parent.mkdir(parents=True, exist_ok=True)
    REPORT.write_text("\n".join(lines), encoding="utf-8", newline="\n")
    print(f"wrote {REPORT.relative_to(ROOT)}")


def service_settings() -> dict[str, str]:
    def env(name: str) -> str:
        return sh("docker", "compose", "exec", "-T", "booking-service", "printenv", name, check=False) or "default"

    return {"workers": env("WEB_CONCURRENCY"), "pool": env("DB_POOL_SIZE"), "overflow": env("DB_MAX_OVERFLOW")}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--seats", type=int, default=500, help="seats on the departure (API maximum 500)")
    parser.add_argument("--vus", type=int, default=100)
    parser.add_argument("--iterations", type=int, default=1000)
    parser.add_argument("--warmup", type=int, default=200)
    parser.add_argument("--runs", type=int, default=1, help="rounds; the report shows the median and every run")
    parser.add_argument("--strategies", nargs="+", default=list(STRATEGIES), choices=STRATEGIES)
    args = parser.parse_args()
    args.warmup = max(args.warmup, args.vus)  # k6: shared-iterations needs at least one per VU
    if not os.environ.get("STRIPE_WEBHOOK_SECRET"):
        raise SystemExit("export STRIPE_WEBHOOK_SECRET (the value the stack was started with)")

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    secret = os.environ.get("JWT_SECRET") or _secret_from_env_file()
    if not secret:
        raise SystemExit("JWT_SECRET not found in the environment or .env")
    operator = mint(secret, "bench-operator", ["OPERATOR"], 3600)
    tokens = [mint(secret, f"bench-user-{i}", ["CUSTOMER"], 3600) for i in range(args.vus)]
    (OUT_DIR / "tokens.json").write_text(json.dumps(tokens))

    tour = api("POST", "/api/tours", operator, {
        "name": "Benchmark Dunes", "description": "contention benchmark", "price": 1850.00,
        "latitude": -24.7275, "longitude": 15.3424,
    })  # fmt: skip
    network = compose_network()
    host = machine()
    run_id = int(time.time())
    base = datetime(2031, 1, 1, 6, 0, tzinfo=UTC) + timedelta(days=run_id % 3000)

    results = []
    slot = 0
    for round_no in range(args.runs):
        # Rotate the order, so no strategy always runs first (cold) or last (warmest).
        order = args.strategies[round_no % len(args.strategies) :] + args.strategies[: round_no % len(args.strategies)]
        for strategy in order:
            print(f"--- round {round_no + 1}: {strategy}")
            restart_booking_service(strategy)
            departures = []
            for seats in (500, args.seats):
                slot += 1
                departures.append(
                    api("POST", "/api/availability", operator, {
                        "tour_id": tour["id"], "starts_at": (base + timedelta(hours=slot)).isoformat(),
                        "total_slots": seats,
                    })["id"]  # fmt: skip
                )
            tag = f"{strategy}-r{round_no + 1}"
            run_k6(network, departures[0], args.vus, args.warmup, f"warmup-{tag}", run_id)
            summary = run_k6(network, departures[1], args.vus, args.iterations, tag, run_id)
            final = final_state(departures[1])
            print(f"final: {final}")
            results.append({"strategy": strategy, "round": round_no, "summary": summary, "final": final})

    service = service_settings()
    restart_booking_service(None)  # back to the compose default
    write_report(results, args, host, service)


if __name__ == "__main__":
    main()
