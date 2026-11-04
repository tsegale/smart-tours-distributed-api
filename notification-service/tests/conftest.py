"""Tests against real infrastructure: Postgres, RabbitMQ (events and Celery), MinIO and Mailpit.

    TEST_DATABASE_URL       postgresql+psycopg://notifications:<pw>@127.0.0.1:5436/notifications_test
    TEST_RABBITMQ_URL       amqp://<user>:<pw>@127.0.0.1:5672/%2F
    TEST_CELERY_BROKER_URL  amqp://<user>:<pw>@127.0.0.1:5672/celery
    TEST_S3_ENDPOINT_URL    http://127.0.0.1:9000, and TEST_S3_ACCESS_KEY / TEST_S3_SECRET_KEY (root)
    TEST_SMTP_HOST / TEST_SMTP_PORT and TEST_MAILPIT_URL (http://127.0.0.1:8025)

docker compose --profile notifications up provides them all (the ports are published on 127.0.0.1); CI runs them as
service containers. The tests need WeasyPrint's system libraries, so they run in the service's test image:
docker build --target test notification-service. Celery runs a real worker (celery.contrib.testing) on the real
broker; eager mode is used only in pure unit tests.
"""

import os
import time
from collections.abc import Callable, Iterator
from pathlib import Path

import pytest

REQUIRED = ("TEST_DATABASE_URL", "TEST_RABBITMQ_URL", "TEST_CELERY_BROKER_URL", "TEST_S3_ENDPOINT_URL",
            "TEST_SMTP_HOST", "TEST_MAILPIT_URL")  # fmt: skip
missing = [name for name in REQUIRED if not os.environ.get(name)]
if missing:
    raise RuntimeError(f"set {', '.join(missing)} (see tests/conftest.py)")

# The application reads its settings once, at import: point them at the test infrastructure first.
KEY_DIR = Path(os.environ.get("TEST_KEY_DIR", "/tmp/notification-test-keys"))
os.environ.update(
    {
        "DATABASE_URL": os.environ["TEST_DATABASE_URL"],
        "RABBITMQ_URL": os.environ["TEST_RABBITMQ_URL"],
        "CELERY_BROKER_URL": os.environ["TEST_CELERY_BROKER_URL"],
        "QUEUE_NAME": "notifications.booking-events.test",
        # Not the live worker's queue: a running notification-worker would otherwise take these tests' tasks.
        "CELERY_TASK_QUEUE": "notifications.tasks.test",
        "S3_ENDPOINT_URL": os.environ["TEST_S3_ENDPOINT_URL"],
        "S3_ACCESS_KEY": os.environ.get("TEST_S3_ACCESS_KEY", "smarttours-admin"),
        "S3_SECRET_KEY": os.environ.get("TEST_S3_SECRET_KEY", ""),
        "TICKETS_BUCKET": "tickets-test",
        "SMTP_HOST": os.environ["TEST_SMTP_HOST"],
        "SMTP_PORT": os.environ.get("TEST_SMTP_PORT", "1025"),
        "TICKET_SIGNING_KEY_FILE": str(KEY_DIR / "signing.pem"),
        # Fast retries, so the retry tests take seconds, not minutes.
        "TASK_RETRY_BACKOFF_S": "1",
        "TASK_RETRY_BACKOFF_MAX_S": "2",
        "TASK_MAX_RETRIES": "3",
        "SEND_RATE_LIMIT": "100/s",
    }
)

import httpx  # noqa: E402
from alembic import command  # noqa: E402
from alembic.config import Config  # noqa: E402
from celery.contrib.testing.worker import start_worker  # noqa: E402
from cryptography.hazmat.primitives import serialization  # noqa: E402
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey  # noqa: E402
from sqlalchemy import text  # noqa: E402

SERVICE_ROOT = Path(__file__).resolve().parents[1]
MAILPIT = os.environ["TEST_MAILPIT_URL"].rstrip("/")


def _write_test_keys() -> Ed25519PrivateKey:
    key = Ed25519PrivateKey.generate()
    KEY_DIR.mkdir(parents=True, exist_ok=True)
    (KEY_DIR / "signing.pem").write_bytes(
        key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption())
    )
    return key


SIGNING_KEY = _write_test_keys()

from app.celery_app import TASK_QUEUE, declare_dead_letter_queue  # noqa: E402
from app.celery_app import app as celery_app  # noqa: E402
from app.database import engine  # noqa: E402


@pytest.fixture(scope="session", autouse=True)
def migrated_database() -> None:
    """The real migrations build the schema, so the tests check the migrations too."""
    with engine.begin() as conn:
        conn.execute(text("DROP SCHEMA public CASCADE"))
        conn.execute(text("CREATE SCHEMA public"))
    cfg = Config(str(SERVICE_ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(SERVICE_ROOT / "migrations"))
    cfg.set_main_option("sqlalchemy.url", os.environ["TEST_DATABASE_URL"].replace("%", "%%"))
    cfg.attributes["configure_logger"] = False
    command.upgrade(cfg, "head")


@pytest.fixture(autouse=True)
def clean_tables() -> None:
    with engine.begin() as conn:
        conn.execute(
            text("TRUNCATE processed_events, booking_snapshots, notifications, tickets, outbox RESTART IDENTITY")
        )


@pytest.fixture
def mailpit() -> Iterator["Mailpit"]:
    box = Mailpit(MAILPIT)
    box.clear()
    box.chaos(None)
    yield box
    box.chaos(None)


@pytest.fixture(scope="session")
def celery_worker() -> Iterator[object]:
    """A real Celery worker on the real broker, in this process (one thread, solo pool)."""
    declare_dead_letter_queue()
    with celery_app.connection_for_write() as connection:
        if _queue_exists(connection, TASK_QUEUE):
            connection.default_channel.queue_purge(TASK_QUEUE)  # tasks left over from an earlier run
    with start_worker(celery_app, pool="solo", concurrency=1, perform_ping_check=False, shutdown_timeout=30) as worker:
        yield worker


def _queue_exists(connection, name: str) -> bool:
    from amqp.exceptions import NotFound

    try:
        connection.default_channel.queue_declare(name, passive=True)
        return True
    except NotFound:
        return False


def eventually(check: Callable[[], object], timeout: float = 60.0, interval: float = 0.25, what: str = "condition"):
    """Poll until check() returns something truthy; the value is returned. Fails with `what` on timeout."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        value = check()
        if value:
            return value
        time.sleep(interval)
    raise AssertionError(f"timed out after {timeout:.0f}s waiting for {what}")


class Mailpit:
    """Mailpit's REST API (v1): what was delivered, and SMTP failures on demand (chaos)."""

    def __init__(self, base_url: str) -> None:
        self._http = httpx.Client(base_url=base_url, timeout=10)

    def clear(self) -> None:
        self._http.delete("/api/v1/messages").raise_for_status()

    def messages(self) -> list[dict]:
        response = self._http.get("/api/v1/messages", params={"limit": 500})
        response.raise_for_status()
        return response.json()["messages"]

    def message(self, message_id: str) -> dict:
        response = self._http.get(f"/api/v1/message/{message_id}")
        response.raise_for_status()
        return response.json()

    def attachment(self, message_id: str, part_id: str) -> bytes:
        response = self._http.get(f"/api/v1/message/{message_id}/part/{part_id}")
        response.raise_for_status()
        return response.content

    def chaos(self, recipient_error: int | None) -> None:
        """Reject every RCPT TO with this SMTP code, or (None) accept everything again."""
        trigger = {"ErrorCode": recipient_error or 451, "Probability": 100 if recipient_error else 0}
        response = self._http.put("/api/v1/chaos", json={"Recipient": trigger})
        response.raise_for_status()
