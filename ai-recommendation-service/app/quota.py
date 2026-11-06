"""A per-user daily token budget in Redis, so one traveller can't spend the LLM bill.

Tokens are reserved before a call, for the most it could cost, and the difference is given back after. Checking after
the fact would let a burst of concurrent requests all pass. The day is Windhoek's. A Redis that can't answer raises:
the caller then plans without the LLM, since an unmetered call is the one thing this exists to prevent.
"""

import logging
from dataclasses import dataclass
from datetime import datetime
from functools import lru_cache
from zoneinfo import ZoneInfo

import redis

from .config import get_settings

log = logging.getLogger(__name__)

WINDHOEK = ZoneInfo("Africa/Windhoek")
EXPIRY_S = 2 * 24 * 3600

# Atomic, so concurrent reservations can't both see room for one.
_RESERVE = """
local used = tonumber(redis.call('GET', KEYS[1]) or '0')
if used + tonumber(ARGV[1]) > tonumber(ARGV[2]) then return -1 end
local total = redis.call('INCRBY', KEYS[1], ARGV[1])
redis.call('EXPIRE', KEYS[1], ARGV[3])
return total
"""


class QuotaUnavailableError(Exception):
    pass


@dataclass(frozen=True)
class Reservation:
    key: str
    tokens: int


class TokenQuota:
    def __init__(self, client: redis.Redis, daily_limit: int) -> None:
        self._redis = client
        self._limit = daily_limit

    @staticmethod
    def _key(user_id: str, now: datetime | None) -> str:
        day = (now or datetime.now(WINDHOEK)).astimezone(WINDHOEK).date().isoformat()
        return f"ai:tokens:{user_id}:{day}"

    def reserve(self, user_id: str, tokens: int, now: datetime | None = None) -> Reservation | None:
        """The reservation, or None if it would take the user over today's limit."""
        key = self._key(user_id, now)
        try:
            total = self._redis.eval(_RESERVE, 1, key, tokens, self._limit, EXPIRY_S)
        except redis.RedisError as exc:
            raise QuotaUnavailableError(type(exc).__name__) from exc
        return None if int(total) < 0 else Reservation(key, tokens)

    def settle(self, reservation: Reservation, used: int) -> None:
        """Give back what wasn't used. Failing only leaves the user's counter high, so a plan already paid for still
        stands."""
        try:
            self._redis.decrby(reservation.key, reservation.tokens - used)  # negative if the estimate was low
        except redis.RedisError as exc:
            log.warning("Couldn't return %d unused tokens: %s", reservation.tokens - used, type(exc).__name__)

    def used(self, user_id: str, now: datetime | None = None) -> int:
        try:
            return int(self._redis.get(self._key(user_id, now)) or 0)
        except redis.RedisError as exc:
            raise QuotaUnavailableError(type(exc).__name__) from exc


@lru_cache
def get_quota() -> TokenQuota:
    settings = get_settings()
    client = redis.Redis.from_url(settings.redis_url, socket_timeout=1, socket_connect_timeout=1)
    return TokenQuota(client, settings.llm_daily_token_limit)
