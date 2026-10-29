"""StripeProvider with the real SDK, and Stripe's HTTP API mocked by respx: the request that
would go to Stripe is checked field by field, and nothing leaves the machine."""

import json
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from urllib.parse import parse_qs

import httpx
import pytest
import respx

from app.config import Settings
from app.errors import InvalidWebhookError, PaymentProviderError
from app.payments.base import CheckoutAlreadyCompletedError, CheckoutRequest, PaymentRef
from app.payments.stripe_provider import StripeProvider
from tests.conftest import WEBHOOK_SECRET
from tests.stripe_events import checkout_event, signature

API = "https://api.stripe.com"
EXPIRES_AT = datetime(2030, 1, 1, 12, 0, tzinfo=UTC)


@pytest.fixture
def stripe_api():
    with respx.mock(base_url=API, assert_all_called=False) as mock:
        yield mock


@pytest.fixture
def provider() -> StripeProvider:
    settings = Settings(
        stripe_secret_key="sk_test_not_a_real_key",
        stripe_webhook_secret=WEBHOOK_SECRET,
        stripe_max_network_retries=0,
    )
    return StripeProvider(settings)


def checkout_request() -> CheckoutRequest:
    return CheckoutRequest(
        booking_id=42,
        description="Sossusvlei Sunrise Dunes, departing 01 Dec 2030 04:00 UTC",
        quantity=2,
        unit_amount=Decimal("1850.00"),
        currency="NAD",
        expires_at=EXPIRES_AT,
        success_url="https://smarttours.example/bookings/42?checkout=success",
        cancel_url="https://smarttours.example/bookings/42?checkout=cancelled",
    )


def session_json(**overrides) -> dict:
    return {
        "id": "cs_test_123",
        "object": "checkout.session",
        "url": "https://checkout.stripe.com/c/pay/cs_test_123",
        "expires_at": int(EXPIRES_AT.timestamp()),
        "amount_total": 370000,
        "currency": "nad",
        "status": "open",
        "payment_intent": None,
        **overrides,
    }


def form(request: httpx.Request) -> dict[str, str]:
    return {k: v[0] for k, v in parse_qs(request.content.decode()).items()}


def test_create_checkout_sends_one_line_item_in_minor_units(provider, stripe_api):
    route = stripe_api.post("/v1/checkout/sessions").mock(return_value=httpx.Response(200, json=session_json()))

    session = provider.create_checkout(checkout_request())

    request = route.calls.last.request
    sent = form(request)
    assert sent == {
        "mode": "payment",
        "submit_type": "book",
        "line_items[0][quantity]": "2",
        "line_items[0][price_data][currency]": "nad",  # NAD is a Stripe presentment currency
        "line_items[0][price_data][unit_amount]": "185000",
        "line_items[0][price_data][product_data][name]": "Sossusvlei Sunrise Dunes, departing 01 Dec 2030 04:00 UTC",
        "client_reference_id": "42",
        "metadata[booking_id]": "42",
        "payment_intent_data[metadata][booking_id]": "42",
        "success_url": "https://smarttours.example/bookings/42?checkout=success",
        "cancel_url": "https://smarttours.example/bookings/42?checkout=cancelled",
        "expires_at": str(int(EXPIRES_AT.timestamp())),
    }
    assert request.headers["Idempotency-Key"] == "booking-42-checkout"
    assert request.headers["Authorization"] == "Bearer sk_test_not_a_real_key"
    assert (session.id, session.url, session.amount_minor, session.currency) == (
        "cs_test_123",
        "https://checkout.stripe.com/c/pay/cs_test_123",
        370000,
        "NAD",
    )
    assert session.expires_at == EXPIRES_AT


def test_stripe_errors_become_a_provider_error(provider, stripe_api):
    stripe_api.post("/v1/checkout/sessions").mock(
        return_value=httpx.Response(500, json={"error": {"type": "api_error", "message": "boom"}})
    )
    with pytest.raises(PaymentProviderError):
        provider.create_checkout(checkout_request())


def test_card_declined_style_errors_become_a_provider_error(provider, stripe_api):
    stripe_api.post("/v1/checkout/sessions").mock(
        return_value=httpx.Response(
            400, json={"error": {"type": "invalid_request_error", "message": "expires_at must be at least 30 minutes"}}
        )
    )
    with pytest.raises(PaymentProviderError):
        provider.create_checkout(checkout_request())


def test_expire_open_session(provider, stripe_api):
    route = stripe_api.post("/v1/checkout/sessions/cs_test_123/expire").mock(
        return_value=httpx.Response(200, json=session_json(status="expired"))
    )
    provider.expire_checkout("cs_test_123")
    assert route.called


def test_expire_completed_session_raises_already_completed(provider, stripe_api):
    stripe_api.post("/v1/checkout/sessions/cs_test_123/expire").mock(
        return_value=httpx.Response(
            400, json={"error": {"type": "invalid_request_error", "message": "Only open sessions can be expired."}}
        )
    )
    stripe_api.get("/v1/checkout/sessions/cs_test_123").mock(
        return_value=httpx.Response(200, json=session_json(status="complete"))
    )
    with pytest.raises(CheckoutAlreadyCompletedError):
        provider.expire_checkout("cs_test_123")


def test_expire_already_expired_session_is_fine(provider, stripe_api):
    stripe_api.post("/v1/checkout/sessions/cs_test_123/expire").mock(
        return_value=httpx.Response(400, json={"error": {"type": "invalid_request_error", "message": "not open"}})
    )
    stripe_api.get("/v1/checkout/sessions/cs_test_123").mock(
        return_value=httpx.Response(200, json=session_json(status="expired"))
    )
    provider.expire_checkout("cs_test_123")


def test_refund_is_idempotent_per_booking(provider, stripe_api):
    route = stripe_api.post("/v1/refunds").mock(
        return_value=httpx.Response(200, json={"id": "re_123", "object": "refund", "status": "succeeded"})
    )

    refund = provider.refund(
        PaymentRef(booking_id=42, checkout_id="cs_test_123", payment_id="pi_123", amount_minor=370000, currency="NAD")
    )

    request = route.calls.last.request
    assert form(request) == {"payment_intent": "pi_123", "amount": "370000", "metadata[booking_id]": "42"}
    assert request.headers["Idempotency-Key"] == "booking-42-refund"
    assert (refund.id, refund.status) == ("re_123", "succeeded")


def test_refund_looks_up_the_payment_intent_when_unknown(provider, stripe_api):
    stripe_api.get("/v1/checkout/sessions/cs_test_123").mock(
        return_value=httpx.Response(200, json=session_json(status="complete", payment_intent="pi_found"))
    )
    route = stripe_api.post("/v1/refunds").mock(
        return_value=httpx.Response(200, json={"id": "re_1", "object": "refund", "status": "pending"})
    )

    provider.refund(PaymentRef(42, "cs_test_123", None, 370000, "NAD"))

    assert form(route.calls.last.request)["payment_intent"] == "pi_found"


def test_parse_webhook_verifies_stripe_signature(provider):
    body = checkout_event("checkout.session.completed", 42, amount_total=370000, event_id="evt_1")

    event = provider.parse_webhook(body, signature(body))

    assert (event.id, event.type, event.checkout_id, event.booking_id) == (
        "evt_1",
        "checkout.session.completed",
        "cs_fake_42",
        42,
    )
    assert (event.payment_status, event.payment_id, event.amount_minor, event.currency) == (
        "paid",
        "pi_test_42",
        370000,
        "NAD",
    )


def test_parse_webhook_rejects_other_secrets(provider):
    body = json.dumps({"id": "evt_1", "type": "x", "data": {"object": {}}}).encode()
    with pytest.raises(InvalidWebhookError):
        provider.parse_webhook(body, signature(body, secret="whsec_someone_else"))


def test_missing_keys_fail_fast():
    with pytest.raises(RuntimeError, match="STRIPE_SECRET_KEY"):
        StripeProvider(Settings(stripe_secret_key="", stripe_webhook_secret=""))


def test_hold_is_sized_to_stripe_minimum_session_lifetime(provider):
    from app.services.booking_service import CHECKOUT_TTL_MARGIN, hold_ttl

    assert hold_ttl(Settings(hold_minutes=30), provider) == timedelta(minutes=30) + CHECKOUT_TTL_MARGIN
    assert hold_ttl(Settings(hold_minutes=5), provider) == timedelta(minutes=30) + CHECKOUT_TTL_MARGIN
    assert hold_ttl(Settings(hold_minutes=60), provider) == timedelta(minutes=60)
    assert hold_ttl(Settings(hold_minutes=48 * 60), provider) == timedelta(hours=24) - CHECKOUT_TTL_MARGIN


def test_respx_blocks_anything_unmocked(provider, stripe_api):
    """Belt and braces: an unexpected Stripe call fails the test instead of reaching the network."""
    with pytest.raises(PaymentProviderError):
        provider.refund(PaymentRef(1, "cs_unmocked", None, 100, "NAD"))
    assert isinstance(stripe_api, respx.MockRouter)
