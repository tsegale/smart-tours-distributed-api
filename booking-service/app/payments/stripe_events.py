"""Stripe-format webhook verification and parsing, shared by both providers.

The fake provider speaks the same wire format (Stripe-Signature header, event JSON), so the
offline flow, the tests and scripts/fake_webhook.py all exercise the real verification path.
"""

import json
from typing import Any

import stripe

from ..errors import InvalidWebhookError
from .base import ProviderEvent

SIGNATURE_TOLERANCE_S = 300  # Stripe's default: reject replays of signatures older than 5 minutes


def parse_stripe_event(raw_body: bytes, signature_header: str | None, secret: str) -> ProviderEvent:
    if not signature_header:
        raise InvalidWebhookError("Missing Stripe-Signature header")
    try:
        # Verifies HMAC-SHA256 over "<timestamp>.<raw body>" with a constant-time compare,
        # and the timestamp tolerance. Must see the exact bytes Stripe sent, so never re-serialise.
        stripe.WebhookSignature.verify_header(
            raw_body.decode("utf-8"), signature_header, secret, tolerance=SIGNATURE_TOLERANCE_S
        )
    except stripe.SignatureVerificationError as exc:
        raise InvalidWebhookError("Webhook signature verification failed") from exc
    except UnicodeDecodeError as exc:
        raise InvalidWebhookError("Webhook body is not UTF-8") from exc

    try:
        event: dict[str, Any] = json.loads(raw_body)
        obj: dict[str, Any] = event.get("data", {}).get("object", {}) or {}
        return ProviderEvent(
            id=str(event["id"]),
            type=str(event["type"]),
            checkout_id=obj.get("id") if obj.get("object") == "checkout.session" else None,
            booking_id=_booking_id(obj),
            payment_status=obj.get("payment_status"),
            payment_id=_id_of(obj.get("payment_intent")),
            amount_minor=obj.get("amount_total"),
            currency=(obj.get("currency") or "").upper() or None,
        )
    except (ValueError, KeyError, TypeError, AttributeError) as exc:
        raise InvalidWebhookError("Webhook body is not a valid event") from exc


def _booking_id(obj: dict[str, Any]) -> int | None:
    raw = (obj.get("metadata") or {}).get("booking_id") or obj.get("client_reference_id")
    return int(raw) if raw is not None and str(raw).isdigit() else None


def _id_of(value: Any) -> str | None:
    # payment_intent is an id string, or an object when the event was expanded.
    if isinstance(value, dict):
        return value.get("id")
    return value
