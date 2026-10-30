"""Tests run against a real PostgreSQL, because row locks and CHECK constraints are the
behaviour under test and SQLite fakes neither.

    docker compose up -d bookings-db
    createdb -h 127.0.0.1 -p 5434 -U bookings bookings_test   # once
    TEST_DATABASE_URL=postgresql+psycopg://bookings:<pw>@127.0.0.1:5434/bookings_test pytest

CI provides the database as a service container. Nothing here talks to Stripe or to
tour-core-service: payments use FakePaymentProvider, tour-core is mocked with respx, and
Stripe's own API is mocked with respx in test_stripe_provider.py.
"""

import os

import pytest

TEST_DATABASE_URL = os.environ.get("TEST_DATABASE_URL")
if not TEST_DATABASE_URL:
    pytest.skip("TEST_DATABASE_URL not set; these tests need a real PostgreSQL", allow_module_level=True)

TOUR_SERVICE_URL = "http://tour-core.test"
WEBHOOK_SECRET = "whsec_test_secret_for_signatures"

# Must happen before the app is imported: the engine and settings are built at import time.
os.environ["DATABASE_URL"] = TEST_DATABASE_URL
os.environ["TOUR_SERVICE_URL"] = TOUR_SERVICE_URL
os.environ["TOUR_SERVICE_BACKOFF_S"] = "0.01"
os.environ["PAYMENT_PROVIDER"] = "fake"
os.environ["STRIPE_WEBHOOK_SECRET"] = WEBHOOK_SECRET
os.environ["STRIPE_SECRET_KEY"] = ""

import respx  # noqa: E402
from alembic import command  # noqa: E402
from alembic.config import Config  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import text  # noqa: E402

from app.config import get_settings  # noqa: E402
from app.database import SessionLocal, engine  # noqa: E402
from app.main import app  # noqa: E402
from app.payments import get_payment_provider  # noqa: E402
from app.payments.fake_provider import FakePaymentProvider  # noqa: E402

SERVICE_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

TOUR = {"id": 1, "name": "Sossusvlei Sunrise Dunes", "price": 1850.00, "currency": "NAD"}


@pytest.fixture(scope="session", autouse=True)
def migrated_database():
    """Build the schema with the real migrations, so the tests also check the migrations."""
    cfg = Config(os.path.join(SERVICE_ROOT, "alembic.ini"))
    cfg.attributes["configure_logger"] = False
    cfg.set_main_option("script_location", os.path.join(SERVICE_ROOT, "migrations"))
    cfg.set_main_option("sqlalchemy.url", TEST_DATABASE_URL.replace("%", "%%"))
    command.downgrade(cfg, "base")
    command.upgrade(cfg, "head")
    yield
    engine.dispose()


@pytest.fixture(autouse=True)
def clean_tables():
    with engine.begin() as conn:
        conn.execute(
            text(
                "TRUNCATE outbox, processed_webhook_events, payments, bookings, tour_availability "
                "RESTART IDENTITY CASCADE"
            )
        )
    yield


LATEST_AVAILABILITY_EVENTS = text(
    """
    SELECT a.id, a.version, a.total_slots - a.booked_slots AS slots_left,
           (e.payload ->> 'version')::int AS event_version, (e.payload ->> 'slotsLeft')::int AS event_slots_left
    FROM tour_availability a
    LEFT JOIN LATERAL (
        SELECT payload FROM outbox o
        WHERE o.event_type = 'availability.changed' AND o.aggregate_id = a.id::text
        ORDER BY (o.payload ->> 'version')::int DESC
        LIMIT 1
    ) e ON true
    ORDER BY a.id
    """
)


@pytest.fixture(autouse=True)
def every_seat_change_is_in_the_outbox(request):
    """After every test: each departure's newest availability.changed event matches the row.

    This is what makes "every change to seat counts writes an outbox row" a checked property of
    the whole suite, not just of the tests that look for events. A path that changes seats
    without calling outbox.availability_changed() leaves the row ahead of its newest event.
    """
    yield
    if request.node.get_closest_marker("raw_seat_writes"):
        return
    with engine.connect() as conn:
        for row in conn.execute(LATEST_AVAILABILITY_EVENTS):
            assert (row.event_version, row.event_slots_left) == (row.version, row.slots_left), (
                f"departure {row.id} is at version {row.version} with {row.slots_left} seats left, but its newest "
                f"availability.changed event says version {row.event_version} with {row.event_slots_left}"
            )


@pytest.fixture(autouse=True)
def tour_core():
    """tour-core-service, mocked. Any tour id resolves to TOUR unless a test overrides the route."""
    with respx.mock(base_url=TOUR_SERVICE_URL, assert_all_called=False) as mock:
        mock.get(path__regex=r"^/api/tours/(?P<tour_id>\d+)$", name="get_tour").mock(
            side_effect=lambda request, tour_id: respx.MockResponse(200, json={**TOUR, "id": int(tour_id)})
        )
        yield mock


@pytest.fixture(autouse=True)
def payments() -> FakePaymentProvider:
    """A fresh fake provider per test, injected wherever the app asks for one."""
    provider = FakePaymentProvider(get_settings())
    app.dependency_overrides[get_payment_provider] = lambda: provider
    yield provider
    app.dependency_overrides.pop(get_payment_provider, None)


@pytest.fixture
def client() -> TestClient:
    return TestClient(app)


@pytest.fixture
def db():
    with SessionLocal() as session:
        yield session


def as_user(user_id: str, *roles: str) -> dict[str, str]:
    """Headers the API gateway would set for an authenticated caller."""
    return {"X-User-Id": user_id, "X-User-Roles": ",".join(roles or ("CUSTOMER",))}


OPERATOR = as_user("operator-1", "OPERATOR")
ALICE = as_user("alice")
BOB = as_user("bob")


@pytest.fixture
def departure(client):
    """Factory: create a departure with N seats and return its id."""

    def _create(total_slots: int = 10, tour_id: int = 1, starts_at: str = "2030-12-01T06:00:00+02:00") -> int:
        response = client.post(
            "/api/availability",
            json={"tour_id": tour_id, "starts_at": starts_at, "total_slots": total_slots},
            headers=OPERATOR,
        )
        assert response.status_code == 201, response.text
        return response.json()["id"]

    return _create


@pytest.fixture
def book(client):
    """Factory: book seats as a user and return the response JSON (asserting success)."""

    def _book(availability_id: int, slots: int = 1, headers: dict[str, str] = ALICE, **extra_headers: str) -> dict:
        response = client.post(
            "/api/bookings",
            json={"availability_id": availability_id, "slots": slots},
            headers={**headers, **extra_headers},
        )
        assert response.status_code in (200, 201), response.text
        return response.json()

    return _book


def remaining(client, tour_id: int = 1) -> int:
    response = client.get("/api/availability", params={"tour_id": tour_id}, headers=ALICE)
    return response.json()[0]["slots_remaining"]
