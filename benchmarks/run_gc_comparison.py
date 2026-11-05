#!/usr/bin/env python3
"""Serial, G1 and generational ZGC for tour-core and the gateway, under their container memory limits.

Needs the catalogue seeded (run_search_benchmark.py --seed) and the images built:

    docker compose build tour-core-service api-gateway
    python benchmarks/run_gc_comparison.py --rate 40 --duration 60s --repeats 2

Per configuration (one JVM's collector changes, the other stays on Serial), repeated and rotated so that no
collector always runs first:
  1. both JVMs are recreated with the collector on the command line, from a generated compose override (it replaces
     the entrypoint, because a second collector flag next to the Dockerfile's would stop the JVM from starting);
  2. start-up: Spring's "Started ... in N seconds", and how long `compose up --wait` took until healthy;
  3. 20 s of the search mix (not reported), then --duration at a fixed --rate through the gateway (k6, open model);
  4. latency from k6; peak RSS from the kernel (VmHWM in /proc/1/status); stop-the-world time from the JVM's
     safepoint log, which every collector writes the same way, and each collection's pause from its GC log, both over
     the measured period only. Each run records when it was measured, to compare with the host's paging.

--append adds to the existing raw results instead of starting over; --configurations limits what runs.

Writes docs/benchmarks/jvm.md between the markers. Standard library only.
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
sys.path.insert(0, str(ROOT / "benchmarks"))
sys.path.insert(0, str(ROOT / "scripts"))
from dev_token import _secret_from_env_file, mint
from run_booking_contention import OUT_DIR, compose_network, env_file_value, machine
from run_integrations_benchmark import vm_memory
from run_search_benchmark import metric, run_k6

REPORT = ROOT / "docs" / "benchmarks" / "jvm.md"
START, END = "<!-- gc:start -->", "<!-- gc:end -->"
OVERRIDE = OUT_DIR / "gc-override.yml"
CORE, GATEWAY = "tour-core-service", "api-gateway"
LOGGING = "-Xlog:safepoint,gc:stdout"
HEAP = "-XX:MaxRAMPercentage=75"
LAUNCHER = "org.springframework.boot.loader.launch.JarLauncher"
COLLECTORS = {
    "Serial": ["-XX:+UseSerialGC"],
    "G1": ["-XX:+UseG1GC"],
    "ZGC": ["-XX:+UseZGC", "-XX:+ZGenerational"],
}
# (tour-core's collector, the gateway's): one changes at a time, against the Serial baseline.
CONFIGURATIONS = [("Serial", "Serial"), ("G1", "Serial"), ("ZGC", "Serial"), ("Serial", "G1"), ("Serial", "ZGC")]


def compose(*args: str, check: bool = True) -> str:
    result = subprocess.run(
        ["docker", "compose", "-f", str(ROOT / "docker-compose.yml"), "-f", str(OVERRIDE), *args],
        cwd=ROOT, capture_output=True, text=True, check=False,
    )  # fmt: skip
    if check and result.returncode != 0:
        raise SystemExit(f"compose {' '.join(args)} failed:\n{result.stdout}\n{result.stderr}")
    return result.stdout


def write_override(core_gc: str, gateway_gc: str) -> None:
    def entrypoint(collector: str) -> str:
        return json.dumps(["java", *COLLECTORS[collector], HEAP, LOGGING, LAUNCHER])

    OVERRIDE.write_text(
        "services:\n"
        f"  {CORE}:\n    entrypoint: {entrypoint(core_gc)}\n"
        f"  {GATEWAY}:\n    entrypoint: {entrypoint(gateway_gc)}\n",
        encoding="utf-8",
    )


def started_seconds(service: str) -> float | None:
    found = re.findall(r"Started \w+ in ([\d.]+) seconds", compose("logs", "--no-log-prefix", service, check=False))
    return float(found[-1]) if found else None


def peak_rss_mib(service: str) -> float:
    status = compose("exec", "-T", service, "cat", "/proc/1/status")
    return int(re.search(r"VmHWM:\s+(\d+) kB", status).group(1)) / 1024


def stop_the_world(service: str, since: str) -> tuple[int, float, float]:
    """(safepoints, total ms, longest ms) in the service's safepoint log since the timestamp."""
    logs = compose("logs", "--no-log-prefix", "--since", since, service, check=False)
    totals = [int(n) / 1_000_000 for n in re.findall(r"Total: (\d+) ns", logs)]
    return len(totals), sum(totals), max(totals, default=0.0)


def collections(service: str, since: str) -> dict[str, float]:
    """Pauses in the service's GC log since the timestamp, e.g. "Pause Full (Allocation Failure) 412M->198M(570M) 812.3ms"."""
    logs = compose("logs", "--no-log-prefix", "--since", since, service, check=False)
    pauses = [(kind, float(ms)) for kind, ms in re.findall(r"Pause (\w+).*? ([\d.]+)ms", logs)]
    full = [ms for kind, ms in pauses if kind == "Full"]
    return {
        "pauses": len(pauses),
        "pause_max_ms": max((ms for _, ms in pauses), default=0.0),
        "full": len(full),
        "full_max_ms": max(full, default=0.0),
    }


def oom_killed(service: str) -> bool:
    container = compose("ps", "-q", service).strip()
    return subprocess.run(["docker", "inspect", "-f", "{{.State.OOMKilled}}", container], capture_output=True,
                          text=True, check=False).stdout.strip() == "true"  # fmt: skip


def run_once(core_gc: str, gateway_gc: str, rate: int, duration: str, network: str) -> dict:
    write_override(core_gc, gateway_gc)
    began = time.monotonic()
    compose("up", "-d", "--no-deps", "--force-recreate", "--wait", CORE, GATEWAY)
    ready_s = time.monotonic() - began
    run_k6(network, "mix", rate, "20s", "gc-tokens.json", "gc-warmup")
    since = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    measured_from = datetime.now().strftime("%H:%M:%S")
    summary = run_k6(network, "mix", rate, duration, "gc-tokens.json", "gc-measured")
    measured_to = datetime.now().strftime("%H:%M:%S")
    latency = "http_req_duration"
    result = {
        "measured_from": measured_from,
        "measured_to": measured_to,
        "core_gc": core_gc,
        "gateway_gc": gateway_gc,
        "ready_s": ready_s,
        "p50": metric(summary, latency, "med"),
        "p95": metric(summary, latency, "p(95)"),
        "p99": metric(summary, latency, "p(99)"),
        "errors": int(metric(summary, "search_errors", "count")),
        "dropped": int(metric(summary, "dropped_iterations", "count")),
    }
    for key, service in (("core", CORE), ("gateway", GATEWAY)):
        safepoints, total, longest = stop_the_world(service, since)
        result |= {
            f"{key}_started_s": started_seconds(service),
            f"{key}_rss": peak_rss_mib(service),
            f"{key}_safepoints": safepoints,
            f"{key}_stw_ms": total,
            f"{key}_stw_max_ms": longest,
            f"{key}_oom": oom_killed(service),
            **{f"{key}_gc_{name}": value for name, value in collections(service, since).items()},
        }
    print(json.dumps(result), flush=True)
    return result


def write_report(results: list[dict], args: argparse.Namespace, memory: tuple[str, str]) -> None:
    info = machine()
    lines = [
        START,
        (f"Generated by `benchmarks/run_gc_comparison.py` on {datetime.now(UTC).date().isoformat()}: the search mix "
        f"through the gateway at a fixed {args.rate} req/s for {args.duration} after 20 s of warm-up, runs shown one by "
        "one (not averaged: see below), each on freshly recreated JVMs. Limits: "
        "tour-core 768 MiB, gateway 512 MiB, heap 75% of the limit. Stop-the-world is the safepoint log over the "
         "measured period."),
        "",
        ("| tour-core | gateway | k6 p50 (ms) | p95 (ms) | p99 (ms) | Errors | Dropped | Core start (s) | Gateway start (s) "
         "| Core peak RSS (MiB) | Gateway peak RSS (MiB) | Core STW total / max (ms) | Gateway STW total / max (ms) "
         "| Core full GCs / longest (ms) |"),
        "| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for core_gc, gateway_gc in CONFIGURATIONS:
        for r in (r for r in results if (r["core_gc"], r["gateway_gc"]) == (core_gc, gateway_gc)):
            lines.append(
                f"| {core_gc} | {gateway_gc} | {r['p50']:.1f} | {r['p95']:.1f} | {r['p99']:.1f} | "
                f"{r['errors']} | {r['dropped']} | {r['core_started_s']:.1f} | {r['gateway_started_s']:.1f} | "
                f"{r['core_rss']:.0f} | {r['gateway_rss']:.0f} | "
                f"{r['core_stw_ms']:.0f} / {r['core_stw_max_ms']:.0f} | "
                f"{r['gateway_stw_ms']:.0f} / {r['gateway_stw_max_ms']:.0f} | "
                + (f"{r['core_gc_full']} / {r['core_gc_full_max_ms']:.0f} |" if "core_gc_full" in r else "not logged |")
            )
    ooms = sum(r["core_oom"] or r["gateway_oom"] for r in results)
    lines += [
        "",
        f"Containers killed for exceeding their limit, across all {len(results)} runs: {ooms}.",
        "",
        "| | |",
        "| --- | --- |",
        f"| Host | {info['host_cpu']} ({info['host_cores']} logical cores), {info['host_ram_gb']} GB RAM, {info['host_os']} |",
        f"| Docker | {info['docker']} |",
        f"| Docker VM memory | before {memory[0]}; after {memory[1]} |",
        "",
        "Raw results: `benchmarks/out/gc-results.json` (not committed).",
        END,
    ]
    text = REPORT.read_text(encoding="utf-8")
    head, rest = text.split(START, 1)
    REPORT.write_text(head + "\n".join(lines) + rest.split(END, 1)[1], encoding="utf-8", newline="\n")
    print(f"wrote {REPORT.relative_to(ROOT)}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--rate", type=int, default=40)
    parser.add_argument("--duration", default="60s")
    parser.add_argument("--repeats", type=int, default=2)
    parser.add_argument("--users", type=int, default=40)
    parser.add_argument("--append", action="store_true", help="add to the raw results instead of starting over")
    parser.add_argument("--configurations", nargs="+", metavar="CORE/GATEWAY", help="e.g. Serial/Serial G1/Serial")
    args = parser.parse_args()

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    secret = env_file_value("JWT_SECRET") or _secret_from_env_file()
    if not secret:
        raise SystemExit("JWT_SECRET not found in .env")
    (OUT_DIR / "gc-tokens.json").write_text(
        json.dumps([mint(secret, f"gc-user-{i}", ["CUSTOMER"], 3 * 3600) for i in range(args.users)])
    )
    network = compose_network()
    memory_before = vm_memory()
    raw = OUT_DIR / "gc-results.json"
    results = json.loads(raw.read_text()) if args.append and raw.exists() else []
    chosen = [tuple(c.split("/")) for c in args.configurations] if args.configurations else CONFIGURATIONS
    for repeat in range(args.repeats):
        shift = repeat % len(chosen)
        for core_gc, gateway_gc in chosen[shift:] + chosen[:shift]:
            results.append(run_once(core_gc, gateway_gc, args.rate, args.duration, network))
            raw.write_text(json.dumps(results, indent=2))
    write_report(results, args, (memory_before, vm_memory()))
    OVERRIDE.unlink(missing_ok=True)
    subprocess.run(["docker", "compose", "up", "-d", "--no-deps", "--force-recreate", "--wait", CORE, GATEWAY],
                   cwd=ROOT, check=True)  # fmt: skip


if __name__ == "__main__":
    main()
