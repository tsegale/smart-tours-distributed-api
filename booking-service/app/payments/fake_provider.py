"""An in-process payment provider for tests, offline development and load tests.

Webhooks use Stripe's wire format and signature scheme with STRIPE_WEBHOOK_SECRET, so
scripts/fake_webhook.py can "pay" a booking end to end without a Stripe account.
"""

from datetime import timedelta

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


class FakePaymentProvider:
    name = "fake"
    min_checkout_ttl = timedelta(0)
    max_checkout_ttl = timedelta(hours=24)

    def __init__(self, settings: Settings) -> None:
        if not settings.stripe_webhook_secret:
            raise RuntimeError("Set STRIPE_WEBHOOK_SECRET (any value) to verify fake webhooks")
        self._webhook_secret = settings.stripe_webhook_secret
        self._checkout_base_url = settings.fake_checkout_base_url.rstrip("/")
        # Test hooks, so failure paths can be driven deterministically.
        self.fail_create = False
        self.fail_refund = False
        self.completed_checkouts: set[str] = set()
        self.created: list[CheckoutRequest] = []
        self.expired: list[str] = []
        self.refunds: list[PaymentRef] = []

    def create_checkout(self, request: CheckoutRequest) -> CheckoutSession:
        if self.fail_create:
            raise PaymentProviderError("Fake provider configured to fail", provider=self.name)
        self.created.append(request)
        checkout_id = f"cs_fake_{request.booking_id}"
        return CheckoutSession(
            id=checkout_id,
            url=f"{self._checkout_base_url}/{checkout_id}",
            expires_at=request.expires_at,
            amount_minor=to_minor_units(request.unit_amount, request.currency) * request.quantity,
            currency=request.currency,
        )

    def expire_checkout(self, checkout_id: str) -> None:
        if checkout_id in self.completed_checkouts:
            raise CheckoutAlreadyCompletedError(checkout_id)
        self.expired.append(checkout_id)

    def refund(self, payment: PaymentRef) -> Refund:
        if self.fail_refund:
            raise PaymentProviderError("Fake provider configured to fail", provider=self.name)
        self.refunds.append(payment)
        return Refund(id=f"re_fake_{payment.booking_id}", status="succeeded")

    def parse_webhook(self, raw_body: bytes, signature_header: str | None) -> ProviderEvent:
        return parse_stripe_event(raw_body, signature_header, self._webhook_secret)
