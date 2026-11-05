"""With the database out of reach, the API fails fast and retryably (found by the alert fire drill)."""

import psycopg
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.exc import OperationalError
from sqlalchemy.exc import TimeoutError as PoolTimeoutError

from app.errors import register_error_handlers


def app_raising(exc: Exception) -> TestClient:
    app = FastAPI()
    register_error_handlers(app)

    @app.get("/boom")
    def boom():
        raise exc

    return TestClient(app, raise_server_exceptions=False)


def unreachable() -> OperationalError:
    """The real error: an engine pointed at a port nothing listens on."""
    engine = create_engine(
        "postgresql+psycopg://x:x@127.0.0.1:1/x", connect_args={"connect_timeout": 1}, pool_pre_ping=True
    )
    with pytest.raises(OperationalError) as caught, engine.connect() as connection:
        connection.execute(text("SELECT 1"))
    return caught.value


def test_an_unreachable_database_is_a_retryable_503():
    response = app_raising(unreachable()).get("/boom")

    assert response.status_code == 503
    assert response.headers["retry-after"] == "5"
    assert response.headers["content-type"] == "application/problem+json"
    assert response.json()["type"].endswith("/database-unavailable")


def test_an_exhausted_pool_is_a_retryable_503():
    response = app_raising(PoolTimeoutError("QueuePool limit reached")).get("/boom")

    assert response.status_code == 503
    assert response.headers["retry-after"] == "5"


def test_an_error_the_server_answered_is_not_disguised_as_an_outage():
    class LockNotAvailable(psycopg.OperationalError):
        sqlstate = "55P03"

    response = app_raising(OperationalError("SELECT 1", {}, LockNotAvailable("lock timeout"))).get("/boom")

    assert response.status_code == 500


def test_the_engine_has_bounded_waits():
    from app.database import engine

    assert engine.pool.timeout() == 5.0
