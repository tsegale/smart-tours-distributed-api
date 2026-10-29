"""The booking race condition, reproduced with real concurrent transactions.

Scenario: a 10-seat departure and 50 customers clicking "Book" at the same moment. Each worker
thread has its own session (its own DB connection and transaction), just as separate web
requests would.
"""

import random
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta

import pytest
from sqlalchemy import func, select, text

from app.config import get_settings
from app.database import SessionLocal
from app.errors import ConflictError, NotEnoughSlotsError
from app.models import Booking, BookingStatus, TourAvailability
from app.schemas import BookingCreate
from app.services import booking_service
from tests.conftest import OPERATOR

HOLD = timedelta(minutes=30)


@pytest.fixture
def make_departure(client):
    def _make(total_slots: int) -> int:
        response = client.post(
            "/api/availability",
            json={"tour_id": 1, "starts_at": "2030-12-01T06:00:00Z", "total_slots": total_slots},
            headers=OPERATOR,
        )
        assert response.status_code == 201, response.text
        return response.json()["id"]

    return _make


def _race(availability_id: int, attempts: int, slots_each: int = 1) -> dict[str, int]:
    start = threading.Barrier(attempts)  # release every thread at the same instant

    def attempt(i: int) -> str:
        start.wait()
        with SessionLocal() as db:
            try:
                booking_service.hold_seats(
                    db,
                    f"user-{i}",
                    BookingCreate(availability_id=availability_id, slots=slots_each),
                    None,
                    HOLD,
                )
                return "booked"
            except NotEnoughSlotsError:
                return "sold_out"
            except ConflictError:
                return "busy"

    with ThreadPoolExecutor(max_workers=attempts) as pool:
        outcomes = list(pool.map(attempt, range(attempts)))
    return {o: outcomes.count(o) for o in ("booked", "sold_out", "busy")}


def _state(availability_id: int) -> tuple[int, int]:
    """(booked_slots on the departure, seats in bookings that hold seats). They must agree."""
    with SessionLocal() as db:
        booked_slots = db.get(TourAvailability, availability_id).booked_slots
        seats_in_bookings = db.scalar(
            select(func.coalesce(func.sum(Booking.slots), 0)).where(
                Booking.availability_id == availability_id,
                Booking.status.in_([BookingStatus.PENDING_PAYMENT, BookingStatus.CONFIRMED]),
            )
        )
        return booked_slots, seats_in_bookings


def test_fifty_concurrent_requests_for_ten_seats_book_exactly_ten(make_departure):
    availability_id = make_departure(total_slots=10)

    outcomes = _race(availability_id, attempts=50)

    assert outcomes == {"booked": 10, "sold_out": 40, "busy": 0}
    assert _state(availability_id) == (10, 10)


def test_multi_seat_requests_never_oversell(make_departure):
    availability_id = make_departure(total_slots=25)

    outcomes = _race(availability_id, attempts=30, slots_each=2)

    assert outcomes["booked"] == 12  # 12 x 2 = 24 seats; the 13th request would need 26
    assert outcomes["sold_out"] == 18
    assert _state(availability_id) == (24, 24)


def test_concurrent_releases_and_reservations_lose_no_updates(make_departure, payments):
    """Cancellations interleaved with new bookings on the same departure: no update is lost."""
    availability_id = make_departure(total_slots=40)
    with SessionLocal() as db:
        holders = [
            booking_service.hold_seats(
                db, f"holder-{i}", BookingCreate(availability_id=availability_id, slots=1), None, HOLD
            )[0].id
            for i in range(20)
        ]
    workers = 40
    start = threading.Barrier(workers)

    def work(i: int) -> str:
        start.wait()
        time.sleep(random.uniform(0, 0.01))
        with SessionLocal() as db:
            if i < 20:
                booking_service.cancel_booking(db, f"holder-{i}", holders[i], payments, get_settings())
                return "cancelled"
            try:
                booking_service.hold_seats(
                    db, f"new-{i}", BookingCreate(availability_id=availability_id, slots=1), None, HOLD
                )
                return "booked"
            except (NotEnoughSlotsError, ConflictError):
                return "rejected"

    with ThreadPoolExecutor(max_workers=workers) as pool:
        outcomes = list(pool.map(work, range(workers)))

    assert outcomes.count("cancelled") == 20
    assert outcomes.count("booked") == 20  # 40 seats, 20 held, 20 released: room for everyone
    assert _state(availability_id) == (20, 20)


def test_check_constraint_is_never_violated_under_contention(make_departure):
    """Hammer a 3-seat departure with 2-seat requests; any oversell would raise IntegrityError."""
    availability_id = make_departure(total_slots=3)

    outcomes = _race(availability_id, attempts=20, slots_each=2)

    assert outcomes["booked"] == 1
    assert _state(availability_id) == (2, 2)


def test_without_the_row_lock_updates_are_lost(make_departure):
    """Control experiment: the naive read-check-write the row lock exists to prevent.

    Every thread reads booked_slots before any of them writes, so each one writes back
    "0 + 1". The counter ends up far below the number of bookings that were created.
    This is the "lost update" anomaly.
    """
    availability_id = make_departure(total_slots=10)
    attempts = 8
    start = threading.Barrier(attempts)

    def naive_book(i: int) -> None:
        start.wait()
        with SessionLocal() as db:
            booked = db.execute(
                text("SELECT booked_slots FROM tour_availability WHERE id = :id"), {"id": availability_id}
            ).scalar_one()
            time.sleep(0.2)  # widen the window between read and write, as real request latency would
            db.execute(
                text("UPDATE tour_availability SET booked_slots = :b WHERE id = :id"),
                {"b": booked + 1, "id": availability_id},
            )
            db.execute(
                text(
                    "INSERT INTO bookings (user_id, availability_id, slots, status, unit_price, total_amount, currency)"
                    " VALUES (:u, :a, 1, 'CONFIRMED', 0, 0, 'NAD')"
                ),
                {"u": f"user-{i}", "a": availability_id},
            )
            db.commit()

    with ThreadPoolExecutor(max_workers=attempts) as pool:
        list(pool.map(naive_book, range(attempts)))

    booked_slots, seats_in_bookings = _state(availability_id)
    assert seats_in_bookings == attempts
    assert booked_slots < seats_in_bookings  # inventory and bookings now disagree
