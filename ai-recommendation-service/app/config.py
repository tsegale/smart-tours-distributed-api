from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "postgresql+psycopg://ai:change-me-ai@127.0.0.1:5435/ai"
    rabbitmq_url: str = "amqp://smarttours:change-me-rabbit@127.0.0.1:5672/%2F"

    # Decided by evaluation (ADR 0020, docs/benchmarks/search-quality.md). "torch" needs requirements-torch.txt.
    embedding_runtime: str = "onnx"
    embedding_model: str = "paraphrase-ml-int8"  # a key of embeddings.MODELS; the database's active model outranks it
    embedding_dimension: int = 384

    rrf_k: int = 60
    default_near_radius_km: float = 100.0

    # Cross-encoder re-ranking, a key of reranker.RERANKERS. Empty: off.
    rerank_model: str = ""
    rerank_depth: int = 50

    # Itinerary planner (ADR 0021). No provider: deterministic plans only, and no key is needed anywhere.
    llm_provider: str = ""  # "", "anthropic", "openai" (any compatible endpoint, Ollama included) or "fake"
    llm_fake_scenario: str = "good"
    llm_max_tokens: int = 16000
    llm_attempt_timeout_s: float = 60.0
    llm_deadline_s: float = 150.0
    llm_daily_token_limit: int = 200_000
    anthropic_api_key: str = ""
    anthropic_model: str = "claude-opus-5-5"
    anthropic_effort: str = "medium"
    anthropic_fallbacks: str = "default"  # server-side refusal fallback; empty turns it off (Haiku has none)
    anthropic_base_url: str = ""
    openai_base_url: str = ""
    openai_api_key: str = ""
    openai_model: str = ""
    redis_url: str = "redis://127.0.0.1:6379/1"

    # Messaging topology (the producer owns only the exchange; this consumer owns the rest).
    events_exchange: str = "smarttours.events"
    queue_name: str = "ai.tour-embedding"
    routing_keys: tuple[str, ...] = (
        "tour.created",
        "tour.updated",
        "availability.changed",
        "review.submitted",
        "booking.confirmed",
        "booking.cancelled",
        "booking.refunded",
    )
    max_retries: int = 5
    retry_delay_ms: int = 10_000


@lru_cache
def get_settings() -> Settings:
    return Settings()
