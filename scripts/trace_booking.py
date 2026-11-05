#!/usr/bin/env python3
"""One booking end to end, then proves its trace is whole: HTTP -> outbox -> relay -> RabbitMQ -> bridge -> render -> email.

Needs the stack with --profile notifications --profile observability (docs/smoke-test.md, section 12). Creates a tour,
a departure and a booking, pays through the signed webhook, waits for the email, then reads the trace back from Tempo
and fails unless every hop is in it and it links to the hold's trace. Prints the trace id. Standard library only.
"""

from __future__ import annotations

import base64
import json
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
from dev_token import _secret_from_env_file, mint

GATEWAY = "http://127.0.0.1:8080"
MAILPIT = "http://127.0.0.1:8025/api/v1"
GRAFANA = "http://127.0.0.1:3001"
TEMPO = f"{GRAFANA}/api/datasources/proxy/uid/tempo/api/traces"
# Which process must appear in the trace, for each hop.
EXPECTED_SERVICES = (
    "api-gateway",
    "booking-service",
    "booking-outbox-relay",
    "notification-bridge",
    "notification-worker",
    "notification-email-worker",
)


def call(
    method: str, url: str, token: str | None = None, body: dict | None = None, headers: dict | None = None
) -> dict:
    request = urllib.request.Request(url, method=method, data=json.dumps(body).encode() if body is not None else None)
    request.add_header("Content-Type", "application/json")
    if token:
        request.add_header("Authorization", f"Bearer {token}")
    for key, value in (headers or {}).items():
        request.add_header(key, value)
    with urllib.request.urlopen(request, timeout=120) as response:
        raw = response.read()
    return json.loads(raw) if raw else {}


def wait_for(what: str, check, timeout_s: float = 120.0):
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        try:
            result = check()
        except (urllib.error.URLError, TimeoutError):
            result = None
        if result:
            return result
        time.sleep(2)
    raise SystemExit(f"timed out waiting for {what}")


def spans_of(trace: dict) -> list[tuple[str, dict]]:
    found = []
    for batch in trace.get("batches", []):
        attributes = batch.get("resource", {}).get("attributes", [])
        service = next((a["value"].get("stringValue") for a in attributes if a["key"] == "service.name"), "?")
        for scope in batch.get("scopeSpans", []):
            found.extend((service, span) for span in scope.get("spans", []))
    return found


def as_hex(trace_id: str) -> str:
    """Tempo's JSON gives ids as base64 or as hex, depending on the endpoint."""
    if len(trace_id) == 32:
        return trace_id
    return base64.b64decode(trace_id).hex()


def main() -> None:
    secret = _secret_from_env_file()
    if not secret:
        raise SystemExit("JWT_SECRET not found in .env")
    operator = mint(secret, "op-trace", ["OPERATOR"], 600)
    email = f"trace-{uuid.uuid4().hex[:8]}@example.com"
    customer = mint(secret, f"trace-{uuid.uuid4().hex[:8]}", ["CUSTOMER"], 600, email)

    tour = call("POST", f"{GATEWAY}/api/tours", operator, {
        "name": "Trace Check Tour", "description": "A tour for the trace check", "price": 1000.00,
        "latitude": -22.56, "longitude": 17.08, "category": "CULTURAL", "difficulty": "EASY", "durationMinutes": 240,
    })  # fmt: skip
    starts_at = (datetime.now(UTC) + timedelta(days=7)).isoformat()
    departure = call("POST", f"{GATEWAY}/api/availability", operator, {
        "tour_id": tour["id"], "starts_at": starts_at, "total_slots": 5,
    })  # fmt: skip
    booking = call("POST", f"{GATEWAY}/api/bookings", customer, {"availability_id": departure["id"], "slots": 1},
                   {"Idempotency-Key": uuid.uuid4().hex})  # fmt: skip

    # The trace we follow starts at the request that confirms the booking: the payment webhook.
    paid_at_ns = time.time_ns()
    subprocess.run([sys.executable, str(ROOT / "scripts" / "fake_webhook.py"), "--booking", str(booking["id"])],
                   check=True, cwd=ROOT, stdout=subprocess.DEVNULL)  # fmt: skip

    wait_for(f"the email to {email}", lambda: call(
        "GET", f"{MAILPIT}/search?query={urllib.parse.quote('to:' + email)}").get("messages"))  # fmt: skip

    def find_trace():
        query = urllib.parse.urlencode({"q": '{ name =~ ".*notifications.send_email.*" }', "limit": 20})
        found = call("GET", f"{GRAFANA}/api/datasources/proxy/uid/tempo/api/search?{query}")
        for candidate in sorted(found.get("traces", []), key=lambda t: int(t["startTimeUnixNano"]), reverse=True):
            if int(candidate["startTimeUnixNano"]) < paid_at_ns:
                continue  # an earlier booking's
            try:
                trace = call("GET", f"{TEMPO}/{candidate['traceID']}")
            except urllib.error.HTTPError:
                continue  # listed before it can be read: the next attempt gets it
            if all(service in {s for s, _ in spans_of(trace)} for service in EXPECTED_SERVICES):
                return candidate["traceID"], trace
        return None

    trace_id, trace = wait_for("the trace in Tempo", find_trace, 300)
    spans = spans_of(trace)
    services = {service for service, _ in spans}
    print(f"trace {trace_id}: {len(spans)} spans")
    for service, span in sorted(spans, key=lambda item: int(item[1]["startTimeUnixNano"])):
        print(f"  {service:28} {span['name']}")
    missing = [s for s in EXPECTED_SERVICES if s not in services]
    if missing:
        raise SystemExit(f"the trace is missing: {', '.join(missing)}")

    # Paying is a later trace than taking the hold: the webhook's span links back to it (ADR 0017).
    links = [link for _, span in spans for link in span.get("links", [])]
    if not links:
        raise SystemExit("no span links: the payment's trace should link to the hold's")
    hold_id = as_hex(links[0]["traceId"])
    hold = call("GET", f"{TEMPO}/{hold_id}")
    print(f"linked to the hold's trace {hold_id}:")
    for service, span in sorted(spans_of(hold), key=lambda item: int(item[1]["startTimeUnixNano"]))[:4]:
        print(f"  {service:28} {span['name']}")
    if hold_id == trace_id or not any(s == "booking-service" for s, _ in spans_of(hold)):
        raise SystemExit("the link doesn't lead to a separate booking-service trace")
    print(f"whole: {GRAFANA}/explore?left=" + urllib.parse.quote(json.dumps({
        "datasource": "tempo", "queries": [{"refId": "A", "queryType": "traceql", "query": trace_id}],
    })))  # fmt: skip


if __name__ == "__main__":
    main()
