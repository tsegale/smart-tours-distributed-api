from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from tests.conftest import ALICE, BOB, OPERATOR, remaining


class TestAuthorization:
    def test_requests_without_gateway_identity_are_rejected(self, client):
        response = client.post("/api/bookings", json={"availability_id": 1})
        assert response.status_code == 401
        assert response.headers["content-type"] == "application/problem+json"

    def test_customers_cannot_create_departures(self, client):
        response = client.post(
            "/api/availability",
            json={"tour_id": 1, "starts_at": "2026-12-01T06:00:00Z", "total_slots": 5},
            headers=ALICE,
        )
        assert response.status_code == 403

    def test_user_id_in_body_is_ignored(self, client, departure):
        """The original service took user_id from the body, so anyone could book as anyone."""
        availability_id = departure()
        response = client.post(
            "/api/bookings", json={"availability_id": availability_id, "user_id": "bob"}, headers=ALICE
        )
        assert response.status_code == 201
        assert client.get("/api/bookings/me", headers=ALICE).json()[0]["id"] == response.json()["id"]
        assert client.get("/api/bookings/me", headers=BOB).json() == []

    def test_users_cannot_read_each_others_bookings(self, client, departure, book):
        booking_id = book(departure())["id"]
        assert client.get(f"/api/bookings/{booking_id}", headers=BOB).status_code == 404
        assert client.post(f"/api/bookings/{booking_id}/cancel", headers=BOB).status_code == 404


class TestBookingHold:
    def test_booking_holds_seats_pending_payment_with_checkout_url(self, client, departure, payments):
        availability_id = departure(total_slots=10)

        before = datetime.now(UTC)
        response = client.post("/api/bookings", json={"availability_id": availability_id, "slots": 3}, headers=ALICE)

        assert response.status_code == 201
        body = response.json()
        assert body["status"] == "PENDING_PAYMENT"
        assert body["slots"] == 3
        assert body["checkout_url"].endswith(f"/cs_fake_{body['id']}")
        assert response.headers["location"] == f"/api/bookings/{body['id']}"
        hold_expires_at = datetime.fromisoformat(body["hold_expires_at"])
        assert before + timedelta(minutes=29) < hold_expires_at <= datetime.now(UTC) + timedelta(minutes=30)
        assert remaining(client) == 7  # held seats are not for sale

    def test_booking_snapshots_price(self, client, departure):
        availability_id = departure()

        body = client.post("/api/bookings", json={"availability_id": availability_id, "slots": 2}, headers=ALICE).json()

        assert (Decimal(body["unit_price"]), Decimal(body["total_amount"]), body["currency"]) == (
            Decimal("1850.00"),
            Decimal("3700.00"),
            "NAD",
        )
        assert body["payment"] == {"provider": "fake", "status": "PENDING", "amount_minor": 370000, "currency": "NAD"}

    def test_checkout_matches_the_hold(self, client, departure, payments):
        availability_id = departure()

        body = client.post("/api/bookings", json={"availability_id": availability_id, "slots": 2}, headers=ALICE).json()

        [request] = payments.created
        assert request.booking_id == body["id"]
        assert request.quantity == 2
        assert request.unit_amount == Decimal("1850.00")
        assert request.currency == "NAD"
        assert "Sossusvlei Sunrise Dunes" in request.description and "01 Dec 2030" in request.description
        assert request.expires_at == datetime.fromisoformat(body["hold_expires_at"])  # never outlives the hold
        assert request.success_url.startswith("http") and str(body["id"]) in request.success_url

    def test_overbooking_is_rejected_with_409(self, client, departure):
        availability_id = departure(total_slots=2)

        response = client.post("/api/bookings", json={"availability_id": availability_id, "slots": 3}, headers=ALICE)

        assert response.status_code == 409
        assert response.json()["slots_remaining"] == 2
        assert remaining(client) == 2

    def test_unknown_departure_is_404(self, client):
        response = client.post("/api/bookings", json={"availability_id": 999}, headers=ALICE)
        assert response.status_code == 404

    def test_invalid_slot_count_is_422(self, client, departure):
        response = client.post("/api/bookings", json={"availability_id": departure(), "slots": 0}, headers=ALICE)
        assert response.status_code == 422
        assert response.json()["errors"][0]["field"] == "body.slots"


class TestCheckoutFailureCompensation:
    def test_provider_failure_releases_the_hold_and_returns_502(self, client, departure, payments, db):
        availability_id = departure(total_slots=5)
        payments.fail_create = True

        response = client.post(
            "/api/bookings",
            json={"availability_id": availability_id, "slots": 2},
            headers={**ALICE, "Idempotency-Key": "k-502"},
        )

        assert response.status_code == 502
        assert response.headers["content-type"] == "application/problem+json"
        assert response.json()["type"].endswith("/payment-provider-error")
        assert remaining(client) == 5  # compensation gave the seats back
        status = db.execute(text("SELECT status FROM bookings")).scalar_one()
        assert status == "CANCELLED"

    def test_same_idempotency_key_can_be_retried_after_compensation(self, client, departure, payments):
        availability_id = departure(total_slots=5)
        headers = {**ALICE, "Idempotency-Key": "k-retry"}
        payments.fail_create = True
        assert (
            client.post("/api/bookings", json={"availability_id": availability_id}, headers=headers).status_code == 502
        )

        payments.fail_create = False
        retry = client.post("/api/bookings", json={"availability_id": availability_id}, headers=headers)

        assert retry.status_code == 201
        assert retry.json()["status"] == "PENDING_PAYMENT"
        assert remaining(client) == 4


class TestIdempotency:
    def test_retry_with_same_key_returns_original_booking_once(self, client, departure, payments):
        availability_id = departure(total_slots=10)
        headers = {**ALICE, "Idempotency-Key": "checkout-123"}

        first = client.post("/api/bookings", json={"availability_id": availability_id, "slots": 2}, headers=headers)
        retry = client.post("/api/bookings", json={"availability_id": availability_id, "slots": 2}, headers=headers)

        assert first.status_code == 201
        assert retry.status_code == 200
        assert retry.json()["id"] == first.json()["id"]
        assert retry.json()["checkout_url"] == first.json()["checkout_url"]
        assert len(payments.created) == 1  # one checkout, not two
        assert remaining(client) == 8  # held once, not twice

    def test_same_key_with_different_payload_is_rejected(self, client, departure):
        availability_id = departure(total_slots=10)
        headers = {**ALICE, "Idempotency-Key": "checkout-123"}

        client.post("/api/bookings", json={"availability_id": availability_id, "slots": 2}, headers=headers)
        response = client.post("/api/bookings", json={"availability_id": availability_id, "slots": 5}, headers=headers)

        assert response.status_code == 422
        assert remaining(client) == 8

    def test_keys_are_scoped_per_user(self, client, departure):
        availability_id = departure(total_slots=10)
        body = {"availability_id": availability_id}

        a = client.post("/api/bookings", json=body, headers={**ALICE, "Idempotency-Key": "k1"})
        b = client.post("/api/bookings", json=body, headers={**BOB, "Idempotency-Key": "k1"})

        assert a.status_code == b.status_code == 201
        assert a.json()["id"] != b.json()["id"]


class TestDepartures:
    def test_departure_snapshots_tour_price(self, client, departure):
        departure()
        [listed] = client.get("/api/availability", params={"tour_id": 1}, headers=ALICE).json()
        assert listed["tour_name"] == "Sossusvlei Sunrise Dunes"
        assert (Decimal(listed["unit_price"]), listed["currency"]) == (Decimal("1850.00"), "NAD")

    def test_duplicate_departure_is_409(self, client, departure):
        departure(tour_id=1, starts_at="2026-12-01T06:00:00Z")
        response = client.post(
            "/api/availability",
            json={"tour_id": 1, "starts_at": "2026-12-01T06:00:00Z", "total_slots": 5},
            headers=OPERATOR,
        )
        assert response.status_code == 409


class TestDatabaseGuards:
    def test_check_constraint_blocks_overbooking_even_without_application_code(self, departure, db):
        """Defence in depth: a buggy code path that skips the lock still can't oversell."""
        availability_id = departure(total_slots=3)
        with pytest.raises(IntegrityError):
            db.execute(text("UPDATE tour_availability SET booked_slots = 4 WHERE id = :id"), {"id": availability_id})
            db.commit()
        db.rollback()

    def test_check_constraint_blocks_a_wrong_total(self, departure, book, db):
        booking_id = book(departure())["id"]
        with pytest.raises(IntegrityError):
            db.execute(text("UPDATE bookings SET total_amount = 1 WHERE id = :id"), {"id": booking_id})
            db.commit()
        db.rollback()
