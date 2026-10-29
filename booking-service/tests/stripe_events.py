"""Builds Stripe-format webhook deliveries and signs them the way Stripe does:

Stripe-Signature: t=<unix time>,v1=<hex HMAC-SHA256(secret, "<t>.<raw body>")>
"""

import hashlib
import hmac
import itertools
import json
import time

from tests.conftest import WEBHOOK_SECRET

_ids = itertools.count(1)


def signature(raw_body: bytes, secret: str = WEBHOOK_SECRET, timestamp: int | None = None) -> str:
    t = int(time.time()) if timestamp is None else timestamp
    mac = hmac.new(secret.encode(), f"{t}.".encode() + raw_body, hashlib.sha256).hexdigest()
    return f"t={t},v1={mac}"


def checkout_event(
    event_type: str,
    booking_id: int,
    *,
    payment_status: str = "paid",
    amount_total: int | None = None,
    event_id: str | None = None,
    payment_intent: str | None = None,
) -> bytes:
    session = {
        "id": f"cs_fake_{booking_id}",
        "object": "checkout.session",
        "client_reference_id": str(booking_id),
        "metadata": {"booking_id": str(booking_id)},
        "payment_status": payment_status,
        "payment_intent": payment_intent or f"pi_test_{booking_id}",
        "currency": "nad",
        "status": "expired" if event_type == "checkout.session.expired" else "complete",
    }
    if amount_total is not None:
        session["amount_total"] = amount_total
    event = {
        "id": event_id or f"evt_test_{next(_ids)}",
        "object": "event",
        "type": event_type,
        "data": {"object": session},
    }
    # Deliberately not compact: the signature must be checked over these exact bytes.
    return json.dumps(event, indent=2).encode()


def deliver(client, raw_body: bytes, signature_header: str | None = None):
    headers = {"Content-Type": "application/json"}
    headers["Stripe-Signature"] = signature_header if signature_header is not None else signature(raw_body)
    return client.post("/webhooks/stripe", content=raw_body, headers=headers)
