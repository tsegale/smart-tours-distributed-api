from functools import lru_cache
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Configuration from environment variables (12-factor). Defaults suit local dev only."""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "postgresql+psycopg://bookings:change-me-bookings@127.0.0.1:5434/bookings"
    db_pool_size: int = 10
    db_max_overflow: int = 20
    # How long a request waits for a contested row lock before giving up with 409.
    lock_timeout_ms: int = 5000

    # --- tour-core-service, called directly on the internal network for price snapshots
    tour_service_url: str = "http://127.0.0.1:8081"
    tour_service_connect_timeout_s: float = 2.0
    tour_service_read_timeout_s: float = 3.0
    # Retries apply to timeouts, connection errors and 5xx only; a 404 is an answer, not a failure.
    tour_service_max_retries: int = Field(default=2, ge=0, le=5)
    tour_service_backoff_s: float = 0.2

    # --- seat holds and the hold sweeper
    hold_minutes: int = Field(default=30, gt=0)
    sweeper_interval_s: float = Field(default=60.0, gt=0)
    sweeper_batch_size: int = Field(default=100, gt=0)

    # --- seat reservation strategy (see ADR 0002 and docs/benchmarks/booking-locking.md)
    reservation_strategy: Literal["pessimistic", "optimistic", "atomic"] = "pessimistic"
    optimistic_max_attempts: int = Field(default=8, gt=0)
    optimistic_backoff_base_ms: float = 5.0
    optimistic_backoff_cap_ms: float = 100.0

    # --- payments
    payment_provider: Literal["fake", "stripe"] = "fake"
    stripe_secret_key: str = ""
    stripe_webhook_secret: str = ""
    stripe_timeout_s: float = 10.0
    stripe_max_network_retries: int = 2
    checkout_success_url: str = "http://localhost:3000/bookings/{booking_id}?checkout=success"
    checkout_cancel_url: str = "http://localhost:3000/bookings/{booking_id}?checkout=cancelled"
    # Base of the pretend hosted checkout page that the fake provider hands out.
    fake_checkout_base_url: str = "http://localhost:8080/fake-checkout"

    # --- cancellation policy
    cancellation_full_refund_hours: int = Field(default=48, ge=0)


@lru_cache
def get_settings() -> Settings:
    return Settings()
