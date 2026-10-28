from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Configuration from environment variables (12-factor). Defaults suit local dev only."""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "postgresql+psycopg://bookings:change-me-bookings@127.0.0.1:5434/bookings"
    db_pool_size: int = 10
    db_max_overflow: int = 20
    # How long a request waits for a contested row lock before giving up with 409.
    lock_timeout_ms: int = 5000


@lru_cache
def get_settings() -> Settings:
    return Settings()
