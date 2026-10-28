import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from tests.conftest import OPERATOR, as_user

ALICE = as_user("alice")
BOB = as_user("bob")


def remaining(client, tour_id: int = 1) -> int:
    response = client.get("/api/availability", params={"tour_id": tour_id}, headers=ALICE)
    return response.json()[0]["slots_remaining"]


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

    def test_users_cannot_read_each_others_bookings(self, client, departure):
        booking_id = client.post("/api/bookings", json={"availability_id": departure()}, headers=ALICE).json()["id"]
        assert client.get(f"/api/bookings/{booking_id}", headers=BOB).status_code == 404
        assert client.post(f"/api/bookings/{booking_id}/cancel", headers=BOB).status_code == 404


class TestBooking:
    def test_booking_reserves_slots(self, client, departure):
        availability_id = departure(total_slots=10)

        response = client.post("/api/bookings", json={"availability_id": availability_id, "slots": 3}, headers=ALICE)

        assert response.status_code == 201
        body = response.json()
        assert body["status"] == "CONFIRMED"
        assert body["slots"] == 3
        assert response.headers["location"] == f"/api/bookings/{body['id']}"
        assert remaining(client) == 7

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


class TestIdempotency:
    def test_retry_with_same_key_returns_original_booking_once(self, client, departure):
        availability_id = departure(total_slots=10)
        headers = {**ALICE, "Idempotency-Key": "checkout-123"}

        first = client.post("/api/bookings", json={"availability_id": availability_id, "slots": 2}, headers=headers)
        retry = client.post("/api/bookings", json={"availability_id": availability_id, "slots": 2}, headers=headers)

        assert first.status_code == 201
        assert retry.status_code == 200
        assert retry.json()["id"] == first.json()["id"]
        assert remaining(client) == 8  # charged once, not twice

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


class TestCancellation:
    def test_cancel_releases_slots_and_is_idempotent(self, client, departure):
        availability_id = departure(total_slots=5)
        booking_id = client.post(
            "/api/bookings", json={"availability_id": availability_id, "slots": 3}, headers=ALICE
        ).json()["id"]
        assert remaining(client) == 2

        first = client.post(f"/api/bookings/{booking_id}/cancel", headers=ALICE)
        second = client.post(f"/api/bookings/{booking_id}/cancel", headers=ALICE)

        assert first.status_code == second.status_code == 200
        assert first.json()["status"] == "CANCELLED"
        assert first.json()["cancelled_at"] is not None
        assert remaining(client) == 5  # released once, not twice


class TestDepartures:
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
