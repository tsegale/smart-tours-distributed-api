"""The booking race condition, reproduced with real concurrent transactions.

Scenario: a 10-seat departure and 50 customers clicking "Book" at the same moment.
Each worker thread has its own session (its own DB connection and transaction), just as
separate web requests would.
"""

import threading
import time
from concurrent.futures import ThreadPoolExecutor

from sqlalchemy import func, select, text

from app.database import SessionLocal
from app.errors import NotEnoughSlotsError
from app.models import Booking, TourAvailability
from app.schemas import AvailabilityCreate, BookingCreate
from app.services import booking_service


def _make_departure(total_slots: int) -> int:
    with SessionLocal() as db:
        availability = booking_service.create_availability(
            db, AvailabilityCreate(tour_id=1, starts_at="2026-12-01T06:00:00Z", total_slots=total_slots)
        )
        return availability.id


def _race(availability_id: int, attempts: int, slots_each: int = 1) -> tuple[int, int]:
    start = threading.Barrier(attempts)  # release every thread at the same instant

    def attempt(i: int) -> str:
        start.wait()
        with SessionLocal() as db:
            try:
                booking_service.create_booking(
                    db, f"user-{i}", BookingCreate(availability_id=availability_id, slots=slots_each), None
                )
                return "booked"
            except NotEnoughSlotsError:
                return "sold_out"

    with ThreadPoolExecutor(max_workers=attempts) as pool:
        outcomes = list(pool.map(attempt, range(attempts)))
    return outcomes.count("booked"), outcomes.count("sold_out")


def _state(availability_id: int) -> tuple[int, int]:
    with SessionLocal() as db:
        booked_slots = db.get(TourAvailability, availability_id).booked_slots
        seats_in_bookings = db.scalar(
            select(func.coalesce(func.sum(Booking.slots), 0)).where(Booking.availability_id == availability_id)
        )
        return booked_slots, seats_in_bookings


def test_fifty_concurrent_requests_for_ten_seats_book_exactly_ten():
    availability_id = _make_departure(total_slots=10)

    booked, sold_out = _race(availability_id, attempts=50)

    assert (booked, sold_out) == (10, 40)
    assert _state(availability_id) == (10, 10)


def test_multi_seat_requests_never_oversell():
    availability_id = _make_departure(total_slots=25)

    booked, sold_out = _race(availability_id, attempts=30, slots_each=2)

    assert booked == 12  # 12 x 2 = 24 seats; the 13th request would need 26
    assert sold_out == 18
    assert _state(availability_id) == (24, 24)


def test_without_the_row_lock_updates_are_lost():
    """Control experiment: the naive read-check-write the lock exists to prevent.

    Every thread reads booked_slots before any of them writes, so each one writes back
    "0 + 1". The counter ends up far below the number of bookings that were created.
    This is the "lost update" anomaly, and why create_booking uses SELECT ... FOR UPDATE.
    """
    availability_id = _make_departure(total_slots=10)
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
            db.add(Booking(user_id=f"user-{i}", availability_id=availability_id, slots=1))
            db.commit()

    with ThreadPoolExecutor(max_workers=attempts) as pool:
        list(pool.map(naive_book, range(attempts)))

    booked_slots, seats_in_bookings = _state(availability_id)
    assert seats_in_bookings == attempts
    assert booked_slots < seats_in_bookings  # inventory and bookings now disagree
