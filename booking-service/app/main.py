import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from sqlalchemy import text

from .database import engine
from .errors import register_error_handlers
from .routers import availability, bookings

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    # Schema is managed by Alembic (`alembic upgrade head`), never by create_all() at import time.
    yield
    engine.dispose()


def create_app() -> FastAPI:
    app = FastAPI(
        title="Smart Tours - Booking Service",
        version="1.0.0",
        description="Departures, seat inventory and bookings with row-level locking against overbooking.",
        lifespan=lifespan,
    )
    register_error_handlers(app)
    app.include_router(bookings.router)
    app.include_router(availability.router)

    @app.get("/health", tags=["ops"])
    def health() -> dict[str, str]:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        return {"status": "UP"}

    return app


app = create_app()
