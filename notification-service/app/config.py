from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Configuration from environment variables (12-factor). Defaults suit local dev only."""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "postgresql+psycopg://notifications:change-me-notifications@127.0.0.1:5436/notifications"
    db_pool_size: int = 5
    db_max_overflow: int = 5

    # --- domain events: the bridge consumes booking.* from the shared topic exchange (ADR 0005)
    rabbitmq_url: str = "amqp://smarttours:change-me-rabbit@127.0.0.1:5672/%2F"
    events_exchange: str = "smarttours.events"
    queue_name: str = "notifications.booking-events"
    max_retries: int = Field(default=5, ge=0)
    retry_delay_ms: int = Field(default=10_000, gt=0)
    prefetch: int = Field(default=10, gt=0)

    # --- Celery: its own vhost, so task traffic never mixes with domain events (ADR 0014)
    celery_broker_url: str = "amqp://smarttours:change-me-rabbit@127.0.0.1:5672/celery"
    task_max_retries: int = Field(default=5, ge=0)
    task_retry_backoff_s: int = Field(default=10, gt=0)
    task_retry_backoff_max_s: int = Field(default=600, gt=0)
    send_rate_limit: str = "20/m"
    # Notifications left PENDING this long (their task was never enqueued, or was lost) are dispatched again.
    redispatch_after_s: int = Field(default=300, gt=0)

    # --- reminders (Celery Beat)
    reminder_interval_s: int = Field(default=900, gt=0)
    reminder_lead_hours: int = Field(default=24, gt=0)
    # Half-width of the window around now + lead. Wider than half the interval, so no departure falls between runs;
    # the unique (booking_id, type) constraint stops the overlap from sending twice.
    reminder_window_minutes: int = Field(default=20, gt=0)

    # --- email
    smtp_host: str = "127.0.0.1"
    smtp_port: int = 1025
    smtp_username: str = ""
    smtp_password: str = ""
    smtp_starttls: bool = False
    smtp_timeout_s: float = 10.0
    mail_from: str = "Smart Tours <bookings@smarttours.example>"
    # Right-hand side of every Message-ID; deterministic ids make a retried send recognisable.
    message_id_domain: str = "notifications.smarttours.example"

    # --- tickets: PDF in S3-compatible object storage (MinIO in compose), signed QR check-in token
    s3_endpoint_url: str = "http://127.0.0.1:9000"
    s3_region: str = "us-east-1"
    s3_access_key: str = "smarttours"
    s3_secret_key: str = "change-me-minio"
    tickets_bucket: str = "tickets"
    ticket_signing_key_file: Path = Path("/run/secrets/ticket_signing_key")
    ticket_key_id: str = "tickets-1"
    # The token stays valid until this long after departure, for late check-ins on multi-day tours.
    ticket_token_grace_hours: int = Field(default=24, gt=0)

    # --- the outbox for ticket.rendered (same envelope and relay design as booking-service, ADR 0008)
    outbox_batch_size: int = Field(default=100, gt=0)
    outbox_relay_interval_s: int = Field(default=30, gt=0)


@lru_cache
def get_settings() -> Settings:
    return Settings()
