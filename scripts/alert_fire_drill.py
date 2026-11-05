#!/usr/bin/env python3
"""Fire each alert rule once by causing what it watches for, and record Grafana saying so (docs/slo.md).

An alert nobody has seen fire may be unable to: one here had a bucket label that matched nothing, and one reset its
timer every time the breaker probed. Each drill makes the real fault, waits until Grafana reports the rule as firing,
then repairs the fault. Writes benchmarks/out/alert-drill.json. Standard library only.

    OBS="--env-file .env --env-file infra/observability/observability.env --profile observability"
    # Core, plus the provider stubs for the breaker:
    COMPOSE_ARGS="$OBS --env-file infra/chaos/chaos.env --profile chaos" python scripts/alert_fire_drill.py dlq relay ...
    # With notifications (reduced settings: docs/smoke-test.md, section 12):
    COMPOSE_ARGS="$OBS --profile notifications" python scripts/alert_fire_drill.py email failed

Drills: dlq relay search booking breaker email failed. "search" fires both search rules.
"""

from __future__ import annotations

import argparse
import http.client
import json
import os
import shlex
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Self

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
from dev_token import _secret_from_env_file, mint

GATEWAY = "http://127.0.0.1:8080"
GRAFANA = "http://127.0.0.1:3001"
MAILPIT = "http://127.0.0.1:8025/api/v1"
RABBIT = "http://127.0.0.1:15672/api"
RESULTS = ROOT / "benchmarks" / "out" / "alert-drill.json"
COMPOSE_ARGS = shlex.split(os.environ.get("COMPOSE_ARGS", ""))
SECRET = _secret_from_env_file()


def env_value(key: str) -> str:
    lines = (ROOT / ".env").read_text(encoding="utf-8").splitlines()
    return next(line.split("=", 1)[1].strip() for line in lines if line.startswith(f"{key}="))


def compose(*args: str, check: bool = True) -> str:
    done = subprocess.run(["docker", "compose", *COMPOSE_ARGS, *args], cwd=ROOT, capture_output=True, text=True, check=False)
    if check and done.returncode != 0:
        raise SystemExit(f"compose {' '.join(args)} failed: {done.stderr[-400:]}")
    return done.stdout


def token(subject: str, *roles: str, email: str | None = None) -> str:
    return mint(SECRET, subject, list(roles) or ["CUSTOMER"], 3600, email)


def call(method: str, url: str, bearer: str | None = None, body: dict | str | None = None, timeout: float = 30,
         headers: dict | None = None) -> tuple[int, bytes]:  # fmt: skip
    data = body.encode() if isinstance(body, str) else json.dumps(body).encode() if body is not None else None
    request = urllib.request.Request(url, method=method, data=data)
    request.add_header("Content-Type", "application/json")
    if bearer:
        request.add_header("Authorization", f"Bearer {bearer}")
    for key, value in (headers or {}).items():
        request.add_header(key, value)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.status, response.read()
    except urllib.error.HTTPError as error:
        return error.code, error.read()
    except (urllib.error.URLError, TimeoutError, ConnectionError, http.client.HTTPException):
        return 0, b""  # no answer: what an outage looks like from here


def rabbit(method: str, path: str, body: dict | None = None) -> tuple[int, bytes]:
    import base64

    auth = base64.b64encode(f"{env_value('RABBITMQ_USER')}:{env_value('RABBITMQ_PASSWORD')}".encode()).decode()
    return call(method, f"{RABBIT}{path}", body=body, headers={"Authorization": f"Basic {auth}"})


def rule_state(title: str) -> dict:
    """The rule as Grafana reports it. A failed or empty answer is "unknown", to be asked again: Grafana can be slow."""
    status, raw = call("GET", f"{GRAFANA}/api/prometheus/grafana/api/v1/rules")
    if status != 200:
        return {"state": "unknown"}
    groups = json.loads(raw)["data"]["groups"]
    for group in groups:
        for rule in group["rules"]:
            if rule["name"] == title:
                return rule
    if groups:
        raise SystemExit(f"no rule named {title}")
    return {"state": "unknown"}


def wait_until_firing(title: str, timeout_s: float) -> dict:
    began = time.monotonic()
    last = None
    while time.monotonic() - began < timeout_s:
        rule = rule_state(title)
        if rule["state"] != last:
            print(f"  {title}: {rule['state']} at {time.monotonic() - began:.0f} s", flush=True)
            last = rule["state"]
        if rule["state"] == "firing":
            alert = next((a for a in rule.get("alerts", []) if a.get("state", "").lower().startswith("alerting")), {})
            return {
                "alert": title,
                "fired": True,
                "seconds_to_fire": round(time.monotonic() - began),
                "at": datetime.now(UTC).isoformat(timespec="seconds"),
                "active_at": alert.get("activeAt"),
                "labels": {
                    k: v for k, v in alert.get("labels", {}).items() if k in ("queue", "name", "job", "service_name")
                },
                "value": alert.get("value"),
            }
        time.sleep(10)
    return {"alert": title, "fired": False, "seconds_to_fire": None, "last_state": last}


class Load:
    """Requests in the background while a fault is on, because burn rates need traffic."""

    def __init__(self, request, threads: int = 4, pause_s: float = 0.2) -> None:
        self._request, self._pause = request, pause_s
        self._stop = threading.Event()
        self._threads = [threading.Thread(target=self._run, daemon=True) for _ in range(threads)]
        self.statuses: dict[int, int] = {}

    def _run(self) -> None:
        while not self._stop.is_set():
            status = self._request()
            self.statuses[status] = self.statuses.get(status, 0) + 1
            self._stop.wait(self._pause)

    def __enter__(self) -> Self:
        for thread in self._threads:
            thread.start()
        return self

    def __exit__(self, *_) -> None:
        self._stop.set()
        for thread in self._threads:
            thread.join(timeout=40)


def search_request(users: list[str]):
    def request() -> int:
        user = users[uuid.uuid4().int % len(users)]
        return call("GET", f"{GATEWAY}/api/tours/search?size=5&q={uuid.uuid4().hex[:8]}", user, timeout=15)[0]

    return request


def new_departure(operator: str, tour_id: int, slots: int = 20) -> int:
    starts_at = (datetime.now(UTC) + timedelta(days=30, seconds=uuid.uuid4().int % 86400)).isoformat()
    status, raw = call("POST", f"{GATEWAY}/api/availability", operator,
                       {"tour_id": tour_id, "starts_at": starts_at, "total_slots": slots})  # fmt: skip
    if status != 201:
        raise SystemExit(f"could not create a departure: {status} {raw[:200]!r}")
    return json.loads(raw)["id"]


def any_tour(operator: str) -> int:
    status, raw = call("POST", f"{GATEWAY}/api/tours", operator, {
        "name": "Fire Drill Tour", "description": "A tour for the alert fire drill", "price": 1000.00,
        "latitude": -22.56, "longitude": 17.08, "category": "CULTURAL", "difficulty": "EASY", "durationMinutes": 240,
    })  # fmt: skip
    if status != 201:
        raise SystemExit(f"could not create a tour: {status} {raw[:200]!r}")
    return json.loads(raw)["id"]


def paid_booking(departure: int, email: str) -> int:
    customer = token(f"drill-{uuid.uuid4().hex[:10]}", email=email)
    status, raw = call("POST", f"{GATEWAY}/api/bookings", customer, {"availability_id": departure, "slots": 1},
                       headers={"Idempotency-Key": uuid.uuid4().hex})  # fmt: skip
    if status not in (200, 201):
        raise SystemExit(f"could not book: {status} {raw[:200]!r}")
    booking = json.loads(raw)["id"]
    subprocess.run([sys.executable, str(ROOT / "scripts" / "fake_webhook.py"), "--booking", str(booking)],
                   check=True, cwd=ROOT, stdout=subprocess.DEVNULL)  # fmt: skip
    return booking


def psql(database_service: str, user_key: str, database: str, sql: str) -> str:
    return compose("exec", "-T", database_service, "psql", "-U", env_value(user_key), "-d", database, "-tAc", sql)


def wait_for_sql(
    database_service: str, user_key: str, database: str, sql: str, what: str, timeout_s: float = 90
) -> str:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        value = psql(database_service, user_key, database, sql).strip()
        if value:
            return value
        time.sleep(2)
    raise SystemExit(f"timed out waiting for {what}")


# ---------------------------------------------------------------- the drills


def drill_dlq() -> list[dict]:
    status, raw = rabbit("POST", "/exchanges/%2F/smarttours.events/publish", {
        "properties": {"content_type": "application/json"}, "routing_key": "availability.changed",
        "payload": "this is not json", "payload_encoding": "string",
    })  # fmt: skip
    print(f"  poison message: {status} {raw[:40]!r}")
    result = wait_until_firing("DLQ not empty", 300)
    rabbit("DELETE", "/queues/%2F/core.availability-summary.dlq/contents")
    return [result]


def drill_relay() -> list[dict]:
    operator = token("drill-op", "OPERATOR")
    tour = any_tour(operator)
    compose("stop", "booking-outbox-relay")
    try:
        new_departure(operator, tour)
        return [wait_until_firing("Outbox relay lag", 360)]
    finally:
        compose("start", "booking-outbox-relay")


def drill_search() -> list[dict]:
    users = [token(f"drill-search-{i}") for i in range(20)]
    request = search_request(users)
    print(f"  healthy baseline: {[request() for _ in range(10)]}")
    time.sleep(30)  # so that the failures below are an increase on a series, and rate() can see them
    compose("stop", "tours-db")
    try:
        with Load(request, threads=6) as load:
            first = wait_until_firing("Search availability burn", 480)
            second = wait_until_firing("Search latency burn", 240)
        print(f"  statuses during the outage: {load.statuses}")
        return [first, second]
    finally:
        compose("up", "-d", "--no-deps", "--wait", "tours-db")


def drill_booking() -> list[dict]:
    operator = token("drill-op", "OPERATOR")
    departure = new_departure(operator, any_tour(operator), slots=500)

    def request() -> int:
        customer = token(f"drill-{uuid.uuid4().hex[:10]}")
        return call("POST", f"{GATEWAY}/api/bookings", customer, {"availability_id": departure, "slots": 1},
                    headers={"Idempotency-Key": uuid.uuid4().hex}, timeout=15)[0]  # fmt: skip

    print(f"  healthy baseline: {[request() for _ in range(5)]}")
    time.sleep(30)
    compose("stop", "bookings-db")
    try:
        with Load(request, threads=4, pause_s=0.5) as load:
            result = wait_until_firing("Booking availability burn", 480)
        print(f"  statuses during the outage: {load.statuses}")
        return [result]
    finally:
        compose("up", "-d", "--no-deps", "--wait", "bookings-db")


def drill_breaker() -> list[dict]:
    operator = token("drill-op", "OPERATOR")
    tours = []
    for index in range(40):  # different places, so the weather cache can't answer for the failing provider
        status, raw = call("POST", f"{GATEWAY}/api/tours", operator, {
            "name": f"Fire Drill {index}", "description": "Breaker drill", "price": 1000.00,
            "latitude": -29.0 + index * 0.37, "longitude": 13.0 + index * 0.29, "category": "CULTURAL",
            "difficulty": "EASY", "durationMinutes": 240,
        })  # fmt: skip
        if status == 201:
            tours.append(json.loads(raw)["id"])
    print(f"  {len(tours)} tours in distinct places")
    subprocess.run([sys.executable, str(ROOT / "scripts" / "chaos.py"), "all", "errors"], check=True, cwd=ROOT,
                   stdout=subprocess.DEVNULL)  # fmt: skip
    user = token("drill-breaker")
    date = (datetime.now(UTC) + timedelta(days=5)).date().isoformat()  # within the forecast's horizon

    def request() -> int:
        tour = tours[uuid.uuid4().int % len(tours)]
        return call("GET", f"{GATEWAY}/api/tours/{tour}/weather?date={date}", user, timeout=15)[0]

    try:
        with Load(request, threads=2, pause_s=1.0) as load:
            result = wait_until_firing("Circuit breaker open", 720)
        print(f"  statuses during the fault: {load.statuses}")
        return [result]
    finally:
        subprocess.run([sys.executable, str(ROOT / "scripts" / "chaos.py"), "all", "healthy"], check=True, cwd=ROOT,
                       stdout=subprocess.DEVNULL)  # fmt: skip


def drill_email() -> list[dict]:
    """Late emails, for real: the worker is paused and their bookings' status change is moved 10 minutes back."""
    operator = token("drill-op", "OPERATOR")
    departure = new_departure(operator, any_tour(operator), slots=100)
    paid_booking(
        departure, f"on-time-{uuid.uuid4().hex[:8]}@example.com"
    )  # puts the counters on the graph at their value
    time.sleep(40)
    compose("pause", "notification-email-worker")
    try:
        bookings = [paid_booking(departure, f"late-{uuid.uuid4().hex[:8]}@example.com") for _ in range(5)]
        ids = ",".join(map(str, bookings))
        wait_for_sql("notifications-db", "NOTIFICATIONS_DB_USER", "notifications",
                     f"SELECT 1 FROM booking_snapshots WHERE booking_id IN ({ids}) HAVING count(*) = {len(bookings)}",
                     "the bridge to record the bookings")  # fmt: skip
        psql("notifications-db", "NOTIFICATIONS_DB_USER", "notifications",
             "UPDATE booking_snapshots SET status_changed_at = status_changed_at - interval '10 minutes' "
             f"WHERE booking_id IN ({ids})")  # fmt: skip
    finally:
        compose("unpause", "notification-email-worker")
    return [wait_until_firing("Email delivery burn", 300)]


def drill_failed() -> list[dict]:
    operator = token("drill-op", "OPERATOR")
    departure = new_departure(operator, any_tour(operator), slots=10)
    call("PUT", f"{MAILPIT}/chaos", body={"Recipient": {"ErrorCode": 550, "Probability": 100}})
    try:
        booking = paid_booking(departure, f"refused-{uuid.uuid4().hex[:8]}@example.com")
        notification = wait_for_sql("notifications-db", "NOTIFICATIONS_DB_USER", "notifications",
                                    f"SELECT id FROM notifications WHERE booking_id = {booking} AND status = 'FAILED'",
                                    "the notification to fail")  # fmt: skip
        result = wait_until_firing("Notifications failed", 300)
    finally:
        call("PUT", f"{MAILPIT}/chaos", body={"Recipient": {"ErrorCode": 451, "Probability": 0}})
    compose("exec", "-T", "notification-service", "python", "-c",
            "import urllib.request as u; u.urlopen(u.Request("
            f"'http://localhost:8084/admin/notifications/{notification}/retry', method='POST', "
            "headers={'X-User-Id':'drill','X-User-Roles':'OPERATOR'}))")  # fmt: skip
    return [result]


DRILLS = {
    "dlq": drill_dlq, "relay": drill_relay, "search": drill_search, "booking": drill_booking,
    "breaker": drill_breaker, "email": drill_email, "failed": drill_failed,
}  # fmt: skip


def wait_until_settled(timeout_s: float = 600) -> None:
    """A drill on a stack that is still starting up measures the start-up."""
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        healthy = call("GET", f"{GATEWAY}/actuator/health", timeout=5)[0] == 200
        load = compose("exec", "-T", "redis", "cat", "/proc/loadavg", check=False).split()
        if healthy and load and float(load[0]) < 12:
            print(f"settled: load {load[0]}", flush=True)
            return
        time.sleep(15)
    raise SystemExit("the stack did not settle")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("drills", nargs="+", choices=list(DRILLS))
    args = parser.parse_args()
    results = json.loads(RESULTS.read_text()) if RESULTS.exists() else {}
    wait_until_settled()
    for name in args.drills:
        print(f"== {name}", flush=True)
        for result in DRILLS[name]():
            results[result["alert"]] = result
            print(f"  {json.dumps(result)}", flush=True)
            RESULTS.parent.mkdir(parents=True, exist_ok=True)
            RESULTS.write_text(json.dumps(results, indent=2))


if __name__ == "__main__":
    main()
