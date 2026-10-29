"""Signed, idempotent payment webhooks, including payments that arrive after the hold lapsed."""

import time
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select

from app.database import SessionLocal
from app.models import Booking, Payment, ProcessedWebhookEvent
from app.services.sweeper import expire_overdue_holds, retry_pending_refunds
from tests.conftest import ALICE, BOB, remaining
from tests.stripe_events import checkout_event, deliver, signature

COMPLETED = "checkout.session.completed"
EXPIRED = "checkout.session.expired"
ASYNC_OK = "checkout.session.async_payment_succeeded"
ASYNC_FAILED = "checkout.session.async_payment_failed"


def state(booking_id: int) -> tuple[str, str | None]:
    with SessionLocal() as db:
        booking = db.get(Booking, booking_id)
        payment = db.execute(select(Payment).where(Payment.booking_id == booking_id)).scalar_one_or_none()
        return booking.status, payment.status if payment else None


def sweep_as_if_later(minutes: int = 31) -> None:
    expire_overdue_holds(SessionLocal, batch_size=100, now=datetime.now(UTC) + timedelta(minutes=minutes))


class TestSignature:
    def test_bad_signature_is_400(self, client, departure, book):
        booking_id = book(departure())["id"]
        body = checkout_event(COMPLETED, booking_id)

        response = deliver(client, body, signature(body, secret="whsec_wrong"))

        assert response.status_code == 400
        assert response.headers["content-type"] == "application/problem+json"
        assert state(booking_id) == ("PENDING_PAYMENT", "PENDING")

    def test_missing_signature_is_400(self, client):
        response = client.post("/webhooks/stripe", content=b"{}", headers={"Content-Type": "application/json"})
        assert response.status_code == 400

    def test_tampered_body_is_400(self, client, departure, book):
        booking_id = book(departure())["id"]
        body = checkout_event(COMPLETED, booking_id)
        header = signature(body)

        response = deliver(client, body.replace(b'"paid"', b'"PAID"'), header)

        assert response.status_code == 400

    def test_signature_over_reformatted_json_is_rejected(self, client, departure, book):
        """The signature is over raw bytes: semantically identical, re-serialised JSON must fail."""
        import json

        booking_id = book(departure())["id"]
        body = checkout_event(COMPLETED, booking_id)
        compact = json.dumps(json.loads(body), separators=(",", ":")).encode()

        assert deliver(client, compact, signature(body)).status_code == 400

    def test_replayed_old_signature_is_400(self, client, departure, book):
        booking_id = book(departure())["id"]
        body = checkout_event(COMPLETED, booking_id)

        response = deliver(client, body, signature(body, timestamp=int(time.time()) - 600))

        assert response.status_code == 400

    def test_non_json_body_with_valid_signature_is_400(self, client):
        body = b"not json"
        assert deliver(client, body).status_code == 400


class TestHappyPath:
    def test_paid_checkout_confirms(self, client, departure, book):
        booking_id = book(departure(total_slots=5), slots=2)["id"]

        response = deliver(client, checkout_event(COMPLETED, booking_id, amount_total=370000))

        assert response.status_code == 200
        assert response.json() == {"received": True, "duplicate": False}
        assert state(booking_id) == ("CONFIRMED", "PAID")
        body = client.get(f"/api/bookings/{booking_id}", headers=ALICE).json()
        assert body["confirmed_at"] is not None
        assert body["checkout_url"] is None and body["hold_expires_at"] is None
        assert remaining(client) == 3  # the held seats became sold seats, not extra ones
        with SessionLocal() as db:
            payment = db.execute(select(Payment)).scalar_one()
            assert payment.provider_payment_id == f"pi_test_{booking_id}"

    def test_unpaid_completion_waits_for_async_result(self, client, departure, book):
        booking_id = book(departure())["id"]

        deliver(client, checkout_event(COMPLETED, booking_id, payment_status="unpaid"))
        assert state(booking_id) == ("PENDING_PAYMENT", "PENDING")

        deliver(client, checkout_event(ASYNC_OK, booking_id))
        assert state(booking_id) == ("CONFIRMED", "PAID")

    def test_async_payment_failure_cancels_and_releases(self, client, departure, book):
        booking_id = book(departure(total_slots=5), slots=2)["id"]
        deliver(client, checkout_event(COMPLETED, booking_id, payment_status="unpaid"))

        deliver(client, checkout_event(ASYNC_FAILED, booking_id, payment_status="unpaid"))

        assert state(booking_id) == ("CANCELLED", "FAILED")
        assert remaining(client) == 5

    def test_expired_checkout_expires_booking_and_releases_seats(self, client, departure, book):
        booking_id = book(departure(total_slots=5), slots=2)["id"]

        deliver(client, checkout_event(EXPIRED, booking_id, payment_status="unpaid"))

        assert state(booking_id) == ("EXPIRED", "EXPIRED")
        assert remaining(client) == 5

    def test_unhandled_event_types_are_acknowledged(self, client):
        body = b'{"id": "evt_other", "type": "customer.created", "data": {"object": {"object": "customer"}}}'
        assert deliver(client, body).status_code == 200

    def test_event_for_unknown_checkout_is_acknowledged(self, client):
        assert deliver(client, checkout_event(COMPLETED, 999_999)).status_code == 200


class TestIdempotency:
    def test_redelivered_event_is_a_no_op(self, client, departure, book):
        booking_id = book(departure(total_slots=5), slots=2)["id"]
        body = checkout_event(EXPIRED, booking_id, event_id="evt_same", payment_status="unpaid")

        first = deliver(client, body)
        second = deliver(client, body)

        assert first.json()["duplicate"] is False
        assert second.status_code == 200 and second.json()["duplicate"] is True
        assert remaining(client) == 5  # released once, not twice
        with SessionLocal() as db:
            assert db.scalar(select(func.count()).select_from(ProcessedWebhookEvent)) == 1

    def test_failed_processing_does_not_mark_the_event_processed(self, client, departure, book, monkeypatch):
        """Dedupe row and state change share a transaction: a crash means the retry still applies."""
        from app.services import webhook_service

        booking_id = book(departure())["id"]
        body = checkout_event(COMPLETED, booking_id, event_id="evt_retry")

        def boom(*_args, **_kwargs):
            raise RuntimeError("database fell over")

        monkeypatch.setattr(webhook_service, "_payment_succeeded", boom)
        crashed = client.__class__(client.app, raise_server_exceptions=False)
        assert deliver(crashed, body).status_code == 500
        monkeypatch.undo()

        assert deliver(client, body).json()["duplicate"] is False
        assert state(booking_id) == ("CONFIRMED", "PAID")

    def test_second_success_event_for_a_confirmed_booking_changes_nothing(self, client, departure, book):
        booking_id = book(departure(total_slots=5), slots=2)["id"]
        deliver(client, checkout_event(COMPLETED, booking_id))

        deliver(client, checkout_event(ASYNC_OK, booking_id))

        assert state(booking_id) == ("CONFIRMED", "PAID")
        assert remaining(client) == 3


class TestLatePayment:
    def test_paid_after_sweep_with_seats_free_re_reserves_and_confirms(self, client, departure, book, payments):
        availability_id = departure(total_slots=5)
        booking_id = book(availability_id, slots=2)["id"]
        sweep_as_if_later()
        assert state(booking_id) == ("EXPIRED", "EXPIRED")
        assert remaining(client) == 5

        deliver(client, checkout_event(COMPLETED, booking_id))

        assert state(booking_id) == ("CONFIRMED", "PAID")
        assert remaining(client) == 3  # seats taken again
        assert payments.refunds == []

    def test_paid_after_sweep_when_sold_out_refunds_automatically(self, client, departure, book, payments):
        availability_id = departure(total_slots=2)
        booking_id = book(availability_id, slots=2)["id"]
        sweep_as_if_later()
        book(availability_id, slots=2, headers=BOB)  # someone else takes the released seats

        response = deliver(client, checkout_event(COMPLETED, booking_id))

        assert response.status_code == 200
        assert state(booking_id) == ("REFUNDED", "REFUNDED")  # refund ran as a background task
        [refund] = payments.refunds
        assert (refund.booking_id, refund.payment_id, refund.amount_minor) == (
            booking_id,
            f"pi_test_{booking_id}",
            370000,
        )
        assert remaining(client) == 0  # Bob keeps his seats; nobody is oversold

    def test_refund_failure_stays_pending_and_the_sweeper_retries_it(self, client, departure, book, payments):
        availability_id = departure(total_slots=1)
        booking_id = book(availability_id)["id"]
        sweep_as_if_later()
        book(availability_id, headers=BOB)
        payments.fail_refund = True

        deliver(client, checkout_event(COMPLETED, booking_id))
        assert state(booking_id) == ("REFUNDED", "REFUND_PENDING")

        payments.fail_refund = False
        later = datetime.now(UTC) + timedelta(minutes=5)
        assert retry_pending_refunds(SessionLocal, payments, batch_size=10, now=later) == 1
        assert state(booking_id) == ("REFUNDED", "REFUNDED")
        assert len(payments.refunds) == 1

    def test_payment_after_cancellation_is_refunded(self, client, departure, book, payments):
        booking_id = book(departure())["id"]
        client.post(f"/api/bookings/{booking_id}/cancel", headers=ALICE)

        deliver(client, checkout_event(COMPLETED, booking_id))

        assert state(booking_id) == ("REFUNDED", "REFUNDED")
        assert len(payments.refunds) == 1
