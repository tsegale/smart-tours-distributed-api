"""Stripe Checkout, through the official SDK (StripeClient, API version pinned by the SDK)."""

import logging
from datetime import UTC, datetime, timedelta

import stripe

from ..config import Settings
from ..domain.money import to_minor_units
from ..errors import PaymentProviderError
from .base import (
    CheckoutAlreadyCompletedError,
    CheckoutRequest,
    CheckoutSession,
    PaymentRef,
    ProviderEvent,
    Refund,
)
from .stripe_events import parse_stripe_event

log = logging.getLogger(__name__)


class StripeProvider:
    name = "stripe"
    # Checkout Session expires_at must be 30 minutes to 24 hours after creation (Stripe API reference).
    min_checkout_ttl = timedelta(minutes=30)
    max_checkout_ttl = timedelta(hours=24)

    def __init__(self, settings: Settings, http_client: stripe.HTTPClient | None = None) -> None:
        if not settings.stripe_secret_key or not settings.stripe_webhook_secret:
            raise RuntimeError("PAYMENT_PROVIDER=stripe needs STRIPE_SECRET_KEY and STRIPE_WEBHOOK_SECRET")
        self._webhook_secret = settings.stripe_webhook_secret
        self._client = stripe.StripeClient(
            settings.stripe_secret_key,
            # httpx, like the rest of the service. The SDK retries network errors and 409/5xx
            # itself, safely, because every mutating call below carries an idempotency key.
            http_client=http_client or stripe.HTTPXClient(timeout=settings.stripe_timeout_s, allow_sync_methods=True),
            max_network_retries=settings.stripe_max_network_retries,
        )

    def create_checkout(self, request: CheckoutRequest) -> CheckoutSession:
        unit_amount = to_minor_units(request.unit_amount, request.currency)
        try:
            session = self._client.v1.checkout.sessions.create(
                params={
                    "mode": "payment",
                    "submit_type": "book",
                    "line_items": [
                        {
                            "quantity": request.quantity,
                            "price_data": {
                                "currency": request.currency.lower(),
                                "unit_amount": unit_amount,
                                "product_data": {"name": request.description},
                            },
                        }
                    ],
                    "client_reference_id": str(request.booking_id),
                    "metadata": {"booking_id": str(request.booking_id)},
                    "payment_intent_data": {"metadata": {"booking_id": str(request.booking_id)}},
                    "success_url": request.success_url,
                    "cancel_url": request.cancel_url,
                    "expires_at": int(request.expires_at.timestamp()),
                },
                # Derived from the booking, and the params are derived from stored state only, so a
                # retry after a timeout returns the same session instead of opening a second one.
                options={"idempotency_key": f"booking-{request.booking_id}-checkout"},
            )
        except stripe.StripeError as exc:
            log.error("Stripe checkout creation failed for booking %s: %s", request.booking_id, exc)
            raise PaymentProviderError("Could not start checkout with Stripe", provider=self.name) from exc
        return CheckoutSession(
            id=session.id,
            url=session.url or "",
            expires_at=datetime.fromtimestamp(session.expires_at, UTC),
            amount_minor=session.amount_total or unit_amount * request.quantity,
            currency=(session.currency or request.currency).upper(),
        )

    def expire_checkout(self, checkout_id: str) -> None:
        try:
            self._client.v1.checkout.sessions.expire(checkout_id)
            return
        except stripe.InvalidRequestError as exc:
            # Only open sessions can be expired. Find out which state it is actually in.
            failure: stripe.StripeError = exc
        except stripe.StripeError as exc:
            raise PaymentProviderError("Could not expire the Stripe checkout", provider=self.name) from exc
        try:
            session = self._client.v1.checkout.sessions.retrieve(checkout_id)
        except stripe.StripeError as exc:
            raise PaymentProviderError("Could not expire the Stripe checkout", provider=self.name) from exc
        if session.status == "expired":
            return
        if session.status == "complete":
            raise CheckoutAlreadyCompletedError(checkout_id) from failure
        raise PaymentProviderError("Could not expire the Stripe checkout", provider=self.name) from failure

    def refund(self, payment: PaymentRef) -> Refund:
        try:
            payment_intent = payment.payment_id
            if payment_intent is None:
                session = self._client.v1.checkout.sessions.retrieve(payment.checkout_id)
                payment_intent = session.payment_intent if isinstance(session.payment_intent, str) else None
            if payment_intent is None:
                raise PaymentProviderError("Checkout has no payment to refund", provider=self.name)
            refund = self._client.v1.refunds.create(
                params={
                    "payment_intent": payment_intent,
                    "amount": payment.amount_minor,
                    "metadata": {"booking_id": str(payment.booking_id)},
                },
                options={"idempotency_key": f"booking-{payment.booking_id}-refund"},
            )
        except stripe.StripeError as exc:
            log.error("Stripe refund failed for booking %s: %s", payment.booking_id, exc)
            raise PaymentProviderError("Could not refund with Stripe", provider=self.name) from exc
        return Refund(id=refund.id, status=refund.status or "pending")

    def parse_webhook(self, raw_body: bytes, signature_header: str | None) -> ProviderEvent:
        return parse_stripe_event(raw_body, signature_header, self._webhook_secret)
