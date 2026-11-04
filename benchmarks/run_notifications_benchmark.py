#!/usr/bin/env python3
"""Notifications under load: N booking.confirmed events in, until every email (with its PDF ticket) is in Mailpit.

Needs the notifications profile running:

    docker compose --profile notifications up -d --wait
    python benchmarks/run_notifications_benchmark.py --events 500 --workers 1,2
    # or one setting per invocation: --workers 1, then --workers 2 --append

Per worker setting (NOTIFICATION_WORKER_CONCURRENCY, Celery prefork processes in one container):
  1. the worker is recreated with that concurrency and the send rate limit lifted (the default 10/s would measure
     the limit, not the pipeline; see the report);
  2. N events are published to smarttours.events with routing key booking.confirmed, from a container on the
     compose network, so the event's occurredAt and the worker's sent_at come from the same clock (the Docker VM's);
  3. it waits until all N notifications are SENT and N emails are in Mailpit, sampling `docker stats` every second.

Reported: end-to-end latency occurredAt -> sent_at (p50/p95/p99, from the database), throughput (N over first
occurredAt to last sent_at), PDF render time per ticket (the worker's ticket.rendered log lines), and peak memory per
container. Writes docs/benchmarks/notifications.md between the bench markers. Standard library only.
"""

from __future__ import annotations

import argparse
import json
import re
import statistics
import subprocess
import sys
import threading
import time
import urllib.request
import uuid
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "benchmarks"))
from memory_footprint import to_mib
from run_booking_contention import OUT_DIR, machine
from run_integrations_benchmark import vm_memory

REPORT = ROOT / "docs" / "benchmarks" / "notifications.md"
START, END = "<!-- bench:start -->", "<!-- bench:end -->"
MAILPIT = "http://127.0.0.1:8025"
WATCHED = ("notification-worker", "notification-bridge", "notifications-db", "rabbitmq", "minio", "mailpit")

PUBLISHER = r"""
import json, os, sys, uuid
from datetime import UTC, datetime, timedelta
import pika
first, count, run = int(sys.argv[1]), int(sys.argv[2]), sys.argv[3]
connection = pika.BlockingConnection(pika.URLParameters(os.environ["RABBITMQ_URL"]))
channel = connection.channel()
channel.confirm_delivery()
starts = datetime.now(UTC) + timedelta(days=20)
for booking_id in range(first, first + count):
    now = datetime.now(UTC)
    event = {
        "eventId": str(uuid.uuid4()), "eventType": "booking.confirmed", "occurredAt": now.isoformat(), "schemaVersion": 1,
        "bookingId": booking_id, "reference": f"ST-{booking_id % 10**8:08d}", "userId": f"bench-{run}",
        "contactEmail": f"bench-{booking_id}@example.com", "status": "CONFIRMED", "previousStatus": "PENDING_PAYMENT",
        "reason": "payment_succeeded", "slots": 2, "unitPrice": "1850.00", "totalAmount": "3700.00", "currency": "NAD",
        "departure": {"availabilityId": 7, "tourId": 42, "tourName": "Sossusvlei Sunrise Dunes",
                      "meetingPoint": "Sesriem gate", "timeZone": "Africa/Windhoek", "startsAt": starts.isoformat()},
    }
    channel.basic_publish("smarttours.events", "booking.confirmed", json.dumps(event).encode(),
                          pika.BasicProperties(content_type="application/json", delivery_mode=2,
                                               message_id=event["eventId"], type="booking.confirmed"))
connection.close()
print("published", count)
"""


def sh(*args: str, check: bool = True, stdin: str | None = None) -> str:
    result = subprocess.run(args, cwd=ROOT, capture_output=True, text=True, input=stdin, check=False)
    if check and result.returncode != 0:
        raise SystemExit(f"command failed ({result.returncode}): {' '.join(args)}\n{result.stdout}\n{result.stderr}")
    return result.stdout.strip()


def compose(*args: str, env_files: list[str], **kwargs) -> str:
    files = [part for f in env_files for part in ("--env-file", f)]
    return sh("docker", "compose", *files, "--profile", "notifications", *args, **kwargs)


def psql(query: str, env_files: list[str]) -> str:
    return compose("exec", "-T", "notifications-db", "psql", "-U", "notifications", "-d", "notifications", "-tA",
                   "-v", "ON_ERROR_STOP=1", "-c", query, env_files=env_files)  # fmt: skip


def mailpit_total() -> int:
    with urllib.request.urlopen(f"{MAILPIT}/api/v1/messages?limit=1", timeout=10) as response:
        return int(json.loads(response.read())["messages_count"])


def mailpit_clear() -> None:
    request = urllib.request.Request(f"{MAILPIT}/api/v1/messages", method="DELETE")
    urllib.request.urlopen(request, timeout=30).close()


class MemorySampler(threading.Thread):
    """Peak `docker stats` memory per compose service while the run lasts."""

    def __init__(self) -> None:
        super().__init__(daemon=True)
        self.peaks: dict[str, float] = {}
        self._done = threading.Event()

    def run(self) -> None:
        while not self._done.is_set():
            lines = sh("docker", "stats", "--no-stream", "--format", "{{.Name}}\t{{.MemUsage}}", check=False)
            for line in lines.splitlines():
                name, usage = line.split("\t")
                service = name.removeprefix(ROOT.name + "-").rsplit("-", 1)[0]
                if service in WATCHED:
                    used = to_mib(usage.split("/")[0])
                    self.peaks[service] = max(self.peaks.get(service, 0.0), used)
            self._done.wait(1.0)

    def stop(self) -> dict[str, float]:
        self._done.set()
        self.join(timeout=10)
        return self.peaks


def percentile(values: list[float], q: float) -> float:
    ordered = sorted(values)
    k = (len(ordered) - 1) * q
    lo, hi = int(k), min(int(k) + 1, len(ordered) - 1)
    return ordered[lo] + (ordered[hi] - ordered[lo]) * (k - lo)


def run_once(concurrency: int, events: int, env_files: list[str], timeout_s: float) -> dict:
    compose("up", "-d", "--no-deps", "--force-recreate", "--wait", "notification-worker", env_files=env_files)
    # Ready means consuming: Celery logs "ready." once the pool is up.
    deadline = time.monotonic() + 120
    while "ready." not in compose("logs", "--since", "5m", "notification-worker", env_files=env_files, check=False):
        if time.monotonic() > deadline:
            raise SystemExit("notification-worker did not become ready")
        time.sleep(1)
    mailpit_clear()
    first = int(time.time()) * 1000  # booking ids unique per run
    run_id = uuid.uuid4().hex[:8]
    started = datetime.now(UTC)
    sampler = MemorySampler()
    sampler.start()
    compose("exec", "-T", "notification-bridge", "python", "-", str(first), str(events), run_id, env_files=env_files,
            stdin=PUBLISHER)  # fmt: skip
    ids = f"booking_id >= {first} AND booking_id < {first + events}"
    deadline = time.monotonic() + timeout_s
    while True:
        sent = int(psql(f"SELECT count(*) FROM notifications WHERE {ids} AND status = 'SENT'", env_files) or 0)
        failed = int(psql(f"SELECT count(*) FROM notifications WHERE {ids} AND status = 'FAILED'", env_files) or 0)
        if sent + failed >= events:
            break
        if time.monotonic() > deadline:
            raise SystemExit(f"timed out: {sent} of {events} sent after {timeout_s:.0f}s")
        time.sleep(1)
    peaks = sampler.stop()
    rows = psql(
        "SELECT EXTRACT(EPOCH FROM n.sent_at - s.status_changed_at) * 1000, "
        "EXTRACT(EPOCH FROM s.status_changed_at), EXTRACT(EPOCH FROM n.sent_at) "
        f"FROM notifications n JOIN booking_snapshots s USING (booking_id) WHERE n.{ids} AND n.status = 'SENT'",
        env_files,
    ).splitlines()
    latencies = [float(r.split("|")[0]) for r in rows]
    first_event = min(float(r.split("|")[1]) for r in rows)
    last_sent = max(float(r.split("|")[2]) for r in rows)
    logs = compose("logs", "--no-log-prefix", "--since", started.strftime("%Y-%m-%dT%H:%M:%SZ"), "notification-worker",
                   env_files=env_files, check=False)  # fmt: skip
    renders = [float(m) for m in re.findall(r"ticket\.rendered booking_id=\d+ bytes=\d+ ms=(\d+)", logs)]
    mailpit = mailpit_total()
    return {
        "concurrency": concurrency,
        "events": events,
        "sent": len(latencies),
        "failed": failed,
        "mailpit": mailpit,
        "p50": percentile(latencies, 0.50),
        "p95": percentile(latencies, 0.95),
        "p99": percentile(latencies, 0.99),
        "max": max(latencies),
        "throughput": len(latencies) / (last_sent - first_event),
        "wall_s": last_sent - first_event,
        "render_p50": statistics.median(renders) if renders else float("nan"),
        "render_p95": percentile(renders, 0.95) if renders else float("nan"),
        "renders": len(renders),
        "peaks": peaks,
    }


def write_report(results: list[dict], args: argparse.Namespace, memory: tuple[str, str]) -> None:
    lines = [
        START,
        (
            f"Generated by `benchmarks/run_notifications_benchmark.py` on {datetime.now(UTC).date().isoformat()}: "
            f"{args.events} `booking.confirmed` events published at once to `smarttours.events`, each with a contact "
            "email, so each renders a PDF ticket (WeasyPrint), stores it in MinIO and sends one email with the PDF "
            "attached to Mailpit. Latency is the event's `occurredAt` to the notification's `sent_at` (same clock). "
            "Send rate limit lifted for the run."
        ),
        "",
        (
            "| Worker processes | Sent | Failed | In Mailpit | p50 (s) | p95 (s) | p99 (s) | Max (s) | Throughput (emails/s) "
            "| PDF render p50 / p95 (ms) |"
        ),
        "| ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | --- |",
    ]
    for r in results:
        lines.append(
            f"| {r['concurrency']} | {r['sent']} | {r['failed']} | {r['mailpit']} | {r['p50'] / 1000:.1f} | "
            f"{r['p95'] / 1000:.1f} | {r['p99'] / 1000:.1f} | {r['max'] / 1000:.1f} | {r['throughput']:.1f} | "
            f"{r['render_p50']:.0f} / {r['render_p95']:.0f} |"
        )
    lines += ["", "Peak memory during each run (MiB, `docker stats` sampled every second):", ""]
    services = sorted({s for r in results for s in r["peaks"]})
    lines.append("| Container | " + " | ".join(f"{r['concurrency']} process(es)" for r in results) + " |")
    lines.append("| --- | " + " | ".join("---:" for _ in results) + " |")
    for service in services:
        lines.append(f"| {service} | " + " | ".join(f"{r['peaks'].get(service, 0):.0f}" for r in results) + " |")
    info = machine()
    lines += [
        "",
        "| | |",
        "| --- | --- |",
        f"| Host | {info['host_cpu']} ({info['host_cores']} logical cores), {info['host_ram_gb']} GB RAM, {info['host_os']} |",
        f"| Docker | {info['docker']} |",
        f"| Docker VM memory | before {memory[0]}; after {memory[1]} |",
        "",
        "Raw results: `benchmarks/out/notifications-results.json` (not committed).",
        END,
    ]
    text = REPORT.read_text(encoding="utf-8") if REPORT.exists() else f"{START}\n{END}\n"
    head, rest = text.split(START, 1)
    tail = rest.split(END, 1)[1]
    REPORT.write_text(head + "\n".join(lines) + tail, encoding="utf-8", newline="\n")
    print(f"wrote {REPORT.relative_to(ROOT)}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--events", type=int, default=500)
    parser.add_argument("--workers", default="1,2", help="comma-separated worker process counts")
    parser.add_argument("--timeout", type=float, default=1800)
    parser.add_argument("--env-file", action="append", default=[], help="extra compose env files, after .env")
    parser.add_argument("--append", action="store_true",
                        help="add to the results of an earlier run (one worker setting per invocation)")  # fmt: skip
    args = parser.parse_args()
    env_files = [str(ROOT / ".env"), *args.env_file]

    import os

    os.environ["NOTIFICATIONS_SEND_RATE_LIMIT"] = "1000/s"
    results_file = OUT_DIR / "notifications-results.json"
    earlier = json.loads(results_file.read_text()) if args.append and results_file.exists() else {}
    memory_before = earlier.get("memory_before") or vm_memory()
    results = earlier.get("results", [])
    for concurrency in (int(w) for w in args.workers.split(",")):
        os.environ["NOTIFICATION_WORKER_CONCURRENCY"] = str(concurrency)
        result = run_once(concurrency, args.events, env_files, args.timeout)
        results.append(result)
        print(f"{concurrency} process(es): sent {result['sent']}/{args.events}, p50 {result['p50'] / 1000:.1f} s, "
              f"p95 {result['p95'] / 1000:.1f} s, {result['throughput']:.1f} emails/s, render p50 "
              f"{result['render_p50']:.0f} ms, peaks {json.dumps({k: round(v) for k, v in result['peaks'].items()})}")  # fmt: skip
    memory_after = vm_memory()
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    results_file.write_text(json.dumps({"memory_before": memory_before, "results": results}, indent=2))
    write_report(results, args, (memory_before, memory_after))
    os.environ.pop("NOTIFICATIONS_SEND_RATE_LIMIT")
    os.environ["NOTIFICATION_WORKER_CONCURRENCY"] = "2"
    compose("up", "-d", "--no-deps", "--force-recreate", "--wait", "notification-worker", env_files=env_files)


if __name__ == "__main__":
    main()
