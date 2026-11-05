from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Response
from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor
from sqlalchemy import text

from .clients.tour_client import close_tour_client
from .database import engine
from .errors import register_error_handlers
from .metrics import render
from .payments import get_payment_provider
from .routers import availability, bookings, checkins, webhooks
from .telemetry import configure_logging, configure_telemetry, instrument_fastapi

configure_logging("booking-service")
configure_telemetry("booking-service")
HTTPXClientInstrumentor().instrument()


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    # Schema is managed by Alembic (`alembic upgrade head`), never by create_all() at import time.
    get_payment_provider()  # fail at start-up, not on the first booking, if payment config is missing
    yield
    close_tour_client()
    engine.dispose()


def create_app() -> FastAPI:
    app = FastAPI(
        title="Smart Tours - Booking Service",
        version="1.0.0",
        description="Departures, seat holds, payment checkout and bookings, without overbooking.",
        lifespan=lifespan,
    )
    register_error_handlers(app)
    app.include_router(bookings.router)
    app.include_router(availability.router)
    app.include_router(webhooks.router)
    app.include_router(checkins.router)

    @app.get("/health", tags=["ops"])
    def health() -> dict[str, str]:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        return {"status": "UP"}

    @app.get("/metrics", include_in_schema=False)
    def metrics() -> Response:
        body, content_type = render()
        return Response(content=body, media_type=content_type)

    instrument_fastapi(app)
    return app


app = create_app()
