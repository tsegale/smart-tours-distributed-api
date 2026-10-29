"""Regression test for a pool deadlock found by the k6 benchmark.

Under load, FastAPI ran the teardown of the sync `get_db` dependency in the threadpool, while
every endpoint returned with a read transaction still open. Finished requests then held their
connections until a thread was free for teardown, and all threads were blocked waiting for a
connection: 30 sessions "idle in transaction", QueuePool timeouts, throughput near zero.

The fix has two halves, both checked here: endpoints end their transaction before returning,
and get_db is an async dependency, so its teardown never needs a thread.
"""

import inspect

import pytest

from app.database import SessionLocal, get_db
from app.main import app
from tests.conftest import ALICE, OPERATOR
from tests.stripe_events import checkout_event, deliver


@pytest.fixture
def open_transactions():
    """Swap in a get_db that records, at teardown, whether the endpoint left a transaction open."""
    seen: list[bool] = []

    async def recording_get_db():
        session = SessionLocal()
        try:
            yield session
        finally:
            seen.append(session.in_transaction())
            session.close()

    app.dependency_overrides[get_db] = recording_get_db
    yield seen
    app.dependency_overrides.pop(get_db, None)


def test_get_db_teardown_does_not_need_the_threadpool():
    assert inspect.isasyncgenfunction(get_db)


def test_no_endpoint_returns_with_a_transaction_open(client, open_transactions):
    availability = client.post(
        "/api/availability",
        json={"tour_id": 1, "starts_at": "2030-12-01T06:00:00Z", "total_slots": 5},
        headers=OPERATOR,
    ).json()
    client.get("/api/availability", params={"tour_id": 1}, headers=ALICE)
    booking = client.post("/api/bookings", json={"availability_id": availability["id"]}, headers=ALICE).json()
    client.post(  # idempotent replay path
        "/api/bookings", json={"availability_id": availability["id"]}, headers={**ALICE, "Idempotency-Key": "k"}
    )
    client.post(
        "/api/bookings", json={"availability_id": availability["id"]}, headers={**ALICE, "Idempotency-Key": "k"}
    )
    client.get(f"/api/bookings/{booking['id']}", headers=ALICE)
    client.get("/api/bookings/me", headers=ALICE)
    client.post("/api/bookings", json={"availability_id": availability["id"], "slots": 99}, headers=ALICE)  # 409
    deliver(client, checkout_event("checkout.session.completed", booking["id"]))
    client.post(f"/api/bookings/{booking['id']}/cancel", headers=ALICE)

    assert len(open_transactions) == 10
    assert not any(open_transactions)


def test_no_connection_is_held_while_the_payment_provider_is_called(client, payments, monkeypatch):
    """A slow PSP must not pin pooled connections: every provider call happens with none checked out."""
    from app.database import engine

    checked_out: dict[str, int] = {}

    def spy(name):
        original = getattr(payments, name)

        def call(*args, **kwargs):
            checked_out[name] = engine.pool.checkedout()
            return original(*args, **kwargs)

        monkeypatch.setattr(payments, name, call)

    for name in ("create_checkout", "expire_checkout", "refund"):
        spy(name)

    availability = client.post(
        "/api/availability",
        json={"tour_id": 1, "starts_at": "2030-12-01T06:00:00Z", "total_slots": 5},
        headers=OPERATOR,
    ).json()
    held = client.post("/api/bookings", json={"availability_id": availability["id"]}, headers=ALICE).json()
    client.post(f"/api/bookings/{held['id']}/cancel", headers=ALICE)  # expire_checkout
    paid = client.post("/api/bookings", json={"availability_id": availability["id"]}, headers=ALICE).json()
    deliver(client, checkout_event("checkout.session.completed", paid["id"]))
    client.post(f"/api/bookings/{paid['id']}/cancel", headers=ALICE)  # refund

    assert checked_out == {"create_checkout": 0, "expire_checkout": 0, "refund": 0}
