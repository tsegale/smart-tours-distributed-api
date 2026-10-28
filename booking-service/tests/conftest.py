"""Tests run against a real PostgreSQL, because row locks and CHECK constraints are the
behaviour under test and SQLite fakes neither.

    docker compose up -d bookings-db
    createdb -h 127.0.0.1 -p 5434 -U bookings bookings_test   # once
    TEST_DATABASE_URL=postgresql+psycopg://bookings:<pw>@127.0.0.1:5434/bookings_test pytest

CI provides the database as a service container.
"""

import os

import pytest

TEST_DATABASE_URL = os.environ.get("TEST_DATABASE_URL")
if not TEST_DATABASE_URL:
    pytest.skip("TEST_DATABASE_URL not set; these tests need a real PostgreSQL", allow_module_level=True)

# Must happen before the app is imported: the engine is built from settings at import time.
os.environ["DATABASE_URL"] = TEST_DATABASE_URL

from alembic import command  # noqa: E402
from alembic.config import Config  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import text  # noqa: E402

from app.database import SessionLocal, engine  # noqa: E402
from app.main import app  # noqa: E402

SERVICE_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


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
        conn.execute(text("TRUNCATE bookings, tour_availability RESTART IDENTITY CASCADE"))
    yield


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


@pytest.fixture
def departure(client):
    """Factory: create a departure with N seats and return its id."""

    def _create(total_slots: int = 10, tour_id: int = 1, starts_at: str = "2026-12-01T06:00:00+02:00") -> int:
        response = client.post(
            "/api/availability",
            json={"tour_id": tour_id, "starts_at": starts_at, "total_slots": total_slots},
            headers=OPERATOR,
        )
        assert response.status_code == 201, response.text
        return response.json()["id"]

    return _create
