import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from sqlalchemy import text

from .clients.tour_client import close_tour_client
from .database import engine
from .errors import register_error_handlers
from .payments import get_payment_provider
from .routers import availability, bookings, webhooks

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")


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

    @app.get("/health", tags=["ops"])
    def health() -> dict[str, str]:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        return {"status": "UP"}

    return app


app = create_app()
