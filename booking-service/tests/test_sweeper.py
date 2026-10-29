"""The hold sweeper: expiry, batching, and two sweepers racing each other."""

import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select, text

from app.database import SessionLocal, engine
from app.models import Booking, Payment, TourAvailability
from app.services.sweeper import expire_overdue_holds
from tests.conftest import as_user
from tests.stripe_events import checkout_event, deliver

LATER = timedelta(minutes=31)


def seats(availability_id: int) -> int:
    with SessionLocal() as db:
        return db.get(TourAvailability, availability_id).booked_slots


def statuses() -> dict[str, int]:
    with SessionLocal() as db:
        rows = db.execute(select(Booking.status, func.count()).group_by(Booking.status)).all()
        return dict(rows)


def test_only_overdue_holds_expire(client, departure, book):
    availability_id = departure(total_slots=10)
    overdue = book(availability_id, slots=2)["id"]
    fresh = book(availability_id, slots=1, headers=as_user("bob"))["id"]
    with engine.begin() as conn:
        conn.execute(
            text("UPDATE bookings SET hold_expires_at = now() + interval '1 hour' WHERE id = :id"), {"id": fresh}
        )

    result = expire_overdue_holds(SessionLocal, batch_size=10, now=datetime.now(UTC) + LATER)

    assert (result.expired, result.seats_released) == (1, 2)
    with SessionLocal() as db:
        assert db.get(Booking, overdue).status == "EXPIRED"
        assert db.get(Booking, fresh).status == "PENDING_PAYMENT"
        assert db.execute(select(Payment.status).where(Payment.booking_id == overdue)).scalar_one() == "EXPIRED"
    assert seats(availability_id) == 1


def test_holds_are_not_expired_early(departure, book):
    availability_id = departure()
    book(availability_id)

    assert expire_overdue_holds(SessionLocal, batch_size=10).expired == 0
    assert seats(availability_id) == 1


def test_confirmed_bookings_are_never_swept(client, departure, book):
    availability_id = departure()
    booking_id = book(availability_id, slots=2)["id"]
    deliver(client, checkout_event("checkout.session.completed", booking_id))

    assert expire_overdue_holds(SessionLocal, batch_size=10, now=datetime.now(UTC) + LATER).expired == 0
    assert seats(availability_id) == 2


def test_batches_cover_every_overdue_hold(departure, book):
    """More holds than one batch, over several departures: all of them go, in several passes."""
    departures = [departure(total_slots=20, starts_at=f"2030-12-0{d}T06:00:00Z") for d in range(1, 4)]
    for availability_id in departures:
        for i in range(7):
            book(availability_id, headers=as_user(f"user-{availability_id}-{i}"))

    result = expire_overdue_holds(SessionLocal, batch_size=3, now=datetime.now(UTC) + LATER)

    assert result.expired == 21
    assert [seats(a) for a in departures] == [0, 0, 0]


def test_two_sweepers_at_once_never_double_release(departure, book):
    """Concurrent sweepers over the same overdue holds. Each hold is expired exactly once, so
    seats are released exactly once: booked_slots ends at the seats of the holds that were
    left alone, never below (the CHECK constraint would also refuse a negative count)."""
    departures = [departure(total_slots=50, starts_at=f"2030-12-0{d}T06:00:00Z") for d in range(1, 4)]
    for availability_id in departures:
        for i in range(12):
            book(availability_id, slots=2, headers=as_user(f"user-{availability_id}-{i}"))
        # One hold per departure that is not overdue and must survive.
        book(availability_id, slots=3, headers=as_user(f"keeper-{availability_id}"))
    with engine.begin() as conn:
        conn.execute(
            text("UPDATE bookings SET hold_expires_at = now() + interval '2 hours' WHERE user_id LIKE 'keeper-%'")
        )

    sweepers = 4
    start = threading.Barrier(sweepers)
    now = datetime.now(UTC) + LATER

    def sweep(_: int):
        start.wait()
        return expire_overdue_holds(SessionLocal, batch_size=5, now=now)

    with ThreadPoolExecutor(max_workers=sweepers) as pool:
        results = list(pool.map(sweep, range(sweepers)))

    assert sum(r.expired for r in results) == 36  # 3 departures x 12, each counted by exactly one sweeper
    assert sum(r.seats_released for r in results) == 72
    assert statuses() == {"EXPIRED": 36, "PENDING_PAYMENT": 3}
    assert [seats(a) for a in departures] == [3, 3, 3]
