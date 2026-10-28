from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "postgresql+psycopg://ai:change-me-ai@127.0.0.1:5435/ai"
    rabbitmq_url: str = "amqp://smarttours:change-me-rabbit@127.0.0.1:5672/%2F"

    # all-MiniLM-L6-v2: small, CPU-friendly, 384-dimensional sentence embeddings.
    embedding_model: str = "sentence-transformers/all-MiniLM-L6-v2"
    embedding_dimension: int = 384

    # Messaging topology (the producer owns only the exchange; this consumer owns the rest).
    events_exchange: str = "smarttours.events"
    queue_name: str = "ai.tour-embedding"
    routing_keys: tuple[str, ...] = ("tour.created", "tour.updated")
    max_retries: int = 5
    retry_delay_ms: int = 10_000


@lru_cache
def get_settings() -> Settings:
    return Settings()
