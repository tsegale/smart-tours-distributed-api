"""Cancelling holds and confirmed bookings, and the refund policy."""

from datetime import UTC, datetime, timedelta
from decimal import Decimal

from app.config import get_settings
from app.services.booking_service import refund_eligible
from tests.conftest import ALICE, remaining
from tests.stripe_events import checkout_event, deliver


def iso(dt: datetime) -> str:
    return dt.isoformat()


def confirmed_booking(client, departure, book, starts_in: timedelta, slots: int = 2) -> int:
    availability_id = departure(total_slots=5, starts_at=iso(datetime.now(UTC) + starts_in))
    booking_id = book(availability_id, slots=slots)["id"]
    deliver(client, checkout_event("checkout.session.completed", booking_id))
    return booking_id


class TestCancelHold:
    def test_cancelling_a_hold_expires_checkout_and_releases_seats(self, client, departure, book, payments):
        booking_id = book(departure(total_slots=5), slots=3)["id"]

        response = client.post(f"/api/bookings/{booking_id}/cancel", headers=ALICE)

        assert response.status_code == 200
        body = response.json()
        assert body["status"] == "CANCELLED"
        assert body["payment"]["status"] == "EXPIRED"
        assert body["cancellation"]["policy"] == "hold_released"
        assert payments.expired == [f"cs_fake_{booking_id}"]
        assert remaining(client) == 5

    def test_cancelling_twice_is_a_no_op(self, client, departure, book):
        booking_id = book(departure(total_slots=5), slots=3)["id"]

        first = client.post(f"/api/bookings/{booking_id}/cancel", headers=ALICE)
        second = client.post(f"/api/bookings/{booking_id}/cancel", headers=ALICE)

        assert first.status_code == second.status_code == 200
        assert second.json()["status"] == "CANCELLED"
        assert second.json()["cancelled_at"] == first.json()["cancelled_at"]
        assert remaining(client) == 5  # released once, not twice

    def test_hold_whose_payment_just_completed_cannot_be_cancelled(self, client, departure, book, payments):
        booking_id = book(departure(total_slots=5), slots=2)["id"]
        payments.completed_checkouts.add(f"cs_fake_{booking_id}")

        response = client.post(f"/api/bookings/{booking_id}/cancel", headers=ALICE)

        assert response.status_code == 409
        assert response.json()["type"].endswith("/payment-in-progress")
        assert remaining(client) == 3  # the seats stay held for the payment that is landing


class TestCancelConfirmed:
    def test_early_cancellation_is_refunded_in_full(self, client, departure, book, payments):
        booking_id = confirmed_booking(client, departure, book, starts_in=timedelta(days=10))

        response = client.post(f"/api/bookings/{booking_id}/cancel", headers=ALICE)

        body = response.json()
        assert response.status_code == 200
        assert body["status"] == "REFUNDED"
        assert body["payment"]["status"] == "REFUNDED"
        assert body["cancellation"]["policy"] == "full_refund"
        assert Decimal(body["cancellation"]["refund_amount"]) == Decimal("3700.00")
        assert body["cancellation"]["refund_status"] == "REFUNDED"
        assert "48 hours" in body["cancellation"]["explanation"]
        [refund] = payments.refunds
        assert refund.amount_minor == 370000
        assert remaining(client) == 5

    def test_late_cancellation_is_not_refunded(self, client, departure, book, payments):
        booking_id = confirmed_booking(client, departure, book, starts_in=timedelta(hours=47))

        body = client.post(f"/api/bookings/{booking_id}/cancel", headers=ALICE).json()

        assert body["status"] == "CANCELLED"
        assert body["payment"]["status"] == "PAID"  # the money stays with the operator
        assert body["cancellation"]["policy"] == "no_refund"
        assert body["cancellation"]["refund_amount"] is None
        assert "no refund" in body["cancellation"]["explanation"]
        assert payments.refunds == []
        assert remaining(client) == 5  # seats are released either way

    def test_refund_failure_still_cancels_and_reports_pending(self, client, departure, book, payments):
        booking_id = confirmed_booking(client, departure, book, starts_in=timedelta(days=10))
        payments.fail_refund = True

        body = client.post(f"/api/bookings/{booking_id}/cancel", headers=ALICE).json()

        assert body["status"] == "REFUNDED"
        assert body["payment"]["status"] == "REFUND_PENDING"
        assert body["cancellation"]["refund_status"] == "REFUND_PENDING"


class TestPolicyBoundary:
    def test_threshold_is_strictly_more_than_the_configured_hours(self):
        settings = get_settings()
        now = datetime(2030, 1, 1, tzinfo=UTC)
        hours = timedelta(hours=settings.cancellation_full_refund_hours)
        assert refund_eligible(now + hours + timedelta(seconds=1), settings, now) is True
        assert refund_eligible(now + hours, settings, now) is False
