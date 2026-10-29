"""Payment provider port. Booking logic talks to this interface only, never to an SDK.

Stripe does not onboard Namibian merchants, so a production deployment here would need a
local PSP. Keeping the provider behind this protocol is what lets one slot in (ADR 0006).
"""

from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal
from enum import StrEnum
from typing import Protocol


@dataclass(frozen=True)
class CheckoutRequest:
    """Everything a provider needs to start a hosted checkout for one booking."""

    booking_id: int
    description: str  # line item name: tour and departure
    quantity: int
    unit_amount: Decimal
    currency: str
    expires_at: datetime
    success_url: str
    cancel_url: str


@dataclass(frozen=True)
class CheckoutSession:
    id: str
    url: str
    expires_at: datetime
    amount_minor: int
    currency: str


@dataclass(frozen=True)
class PaymentRef:
    """Identifies a captured payment to refund."""

    booking_id: int
    checkout_id: str
    payment_id: str | None
    amount_minor: int
    currency: str


@dataclass(frozen=True)
class Refund:
    id: str
    status: str  # provider's own status, e.g. "succeeded" or "pending"


class ProviderEventType(StrEnum):
    CHECKOUT_COMPLETED = "checkout.session.completed"
    ASYNC_PAYMENT_SUCCEEDED = "checkout.session.async_payment_succeeded"
    ASYNC_PAYMENT_FAILED = "checkout.session.async_payment_failed"
    CHECKOUT_EXPIRED = "checkout.session.expired"


@dataclass(frozen=True)
class ProviderEvent:
    id: str
    type: str  # a ProviderEventType value, or any other type the provider sends (ignored)
    checkout_id: str | None = None
    booking_id: int | None = None
    payment_status: str | None = None  # "paid", "unpaid" or "no_payment_required"
    payment_id: str | None = None
    amount_minor: int | None = None
    currency: str | None = None


class CheckoutAlreadyCompletedError(Exception):
    """expire_checkout() on a session the customer has already paid or is paying."""


class PaymentProvider(Protocol):
    name: str
    # Bounds the provider puts on a checkout's lifetime. The hold is sized to fit inside them.
    min_checkout_ttl: timedelta
    max_checkout_ttl: timedelta

    def create_checkout(self, request: CheckoutRequest) -> CheckoutSession: ...

    def expire_checkout(self, checkout_id: str) -> None:
        """Close an open checkout so it can no longer be paid. Raises CheckoutAlreadyCompletedError."""
        ...

    def refund(self, payment: PaymentRef) -> Refund: ...

    def parse_webhook(self, raw_body: bytes, signature_header: str | None) -> ProviderEvent:
        """Verify the signature over the raw bytes and parse. Raises InvalidWebhookError."""
        ...
