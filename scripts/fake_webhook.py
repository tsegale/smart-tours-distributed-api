#!/usr/bin/env python3
"""Send a signed, Stripe-format Checkout webhook to the gateway, as Stripe would.

For PAYMENT_PROVIDER=fake: this is how a booking gets "paid" (or expired) without a Stripe
account. The event is signed with STRIPE_WEBHOOK_SECRET exactly like Stripe signs its own
(Stripe-Signature: t=<time>,v1=<HMAC-SHA256>), so it goes through the real verification path.
Standard library only, like dev_token.py.

    python scripts/fake_webhook.py --booking 12                        # checkout.session.completed, paid
    python scripts/fake_webhook.py --booking 12 --type checkout.session.expired
    python scripts/fake_webhook.py --booking 12 --event-id evt_same    # send twice to see dedupe

STRIPE_WEBHOOK_SECRET is read from the environment, falling back to the repo's .env file.
"""
from __future__ import annotations

import argparse
import hashlib
import hmac
import json
import os
import sys
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path

TYPES = (
    "checkout.session.completed",
    "checkout.session.async_payment_succeeded",
    "checkout.session.async_payment_failed",
    "checkout.session.expired",
)


def _secret_from_env_file() -> str | None:
    env_file = Path(__file__).resolve().parent.parent / ".env"
    if not env_file.exists():
        return None
    for line in env_file.read_text().splitlines():
        if line.startswith("STRIPE_WEBHOOK_SECRET="):
            return line.split("=", 1)[1].strip()
    return None


def build_event(event_type: str, booking_id: int, payment_status: str, event_id: str) -> bytes:
    session = {
        "id": f"cs_fake_{booking_id}",  # the id FakePaymentProvider gave the checkout
        "object": "checkout.session",
        "client_reference_id": str(booking_id),
        "metadata": {"booking_id": str(booking_id)},
        "payment_status": payment_status,
        "payment_intent": f"pi_fake_{booking_id}",
        "status": "expired" if event_type.endswith("expired") else "complete",
    }
    event = {"id": event_id, "object": "event", "type": event_type, "data": {"object": session}}
    return json.dumps(event, indent=2).encode()


def sign(body: bytes, secret: str) -> str:
    timestamp = int(time.time())
    mac = hmac.new(secret.encode(), f"{timestamp}.".encode() + body, hashlib.sha256).hexdigest()
    return f"t={timestamp},v1={mac}"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--booking", type=int, required=True, help="booking id")
    parser.add_argument("--type", default=TYPES[0], choices=TYPES)
    parser.add_argument("--payment-status", default=None, help="paid | unpaid (default: by event type)")
    parser.add_argument("--event-id", default=None, help="reuse an id to test deduplication")
    parser.add_argument("--url", default="http://127.0.0.1:8080/webhooks/stripe")
    args = parser.parse_args()

    secret = os.environ.get("STRIPE_WEBHOOK_SECRET") or _secret_from_env_file()
    if not secret:
        raise SystemExit("STRIPE_WEBHOOK_SECRET is not set (environment or .env)")
    payment_status = args.payment_status or ("unpaid" if args.type.endswith(("expired", "failed")) else "paid")
    body = build_event(args.type, args.booking, payment_status, args.event_id or f"evt_fake_{uuid.uuid4().hex}")

    request = urllib.request.Request(
        args.url,
        data=body,
        method="POST",
        headers={"Content-Type": "application/json", "Stripe-Signature": sign(body, secret)},
    )
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            print(response.status, response.read().decode())
    except urllib.error.HTTPError as exc:
        print(exc.code, exc.read().decode())
        sys.exit(1)


if __name__ == "__main__":
    main()
