"""The public keys that verify check-in tokens, fetched from notification-service's JWKS and chosen by `kid`.

- Cached for `ticket_jwks_ttl_s`; a token with a kid the cache doesn't know triggers a refetch (that is how a newly
  rotated key is picked up), at most once per `ticket_jwks_min_refetch_s`, so tokens with made-up kids can't turn
  every scan into a request to notification-service.
- If a refetch fails, the last key set that was fetched stays in use: an outage of notification-service doesn't stop
  check-in at the gate for keys already known. An unknown kid while the last fetch failed is an outage too (503),
  not an invalid ticket: it may be signed with a key rotated in since.
- Only Ed25519 keys for EdDSA are accepted from the set; anything else in it is ignored.
"""

import logging
import threading
import time
from collections.abc import Callable
from functools import lru_cache

import httpx
import jwt
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from ..config import Settings, get_settings
from ..errors import TicketsUnavailableError

log = logging.getLogger(__name__)


class TicketKeySet:
    def __init__(
        self,
        settings: Settings,
        transport: httpx.BaseTransport | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._url = settings.ticket_jwks_url
        self._ttl_s = settings.ticket_jwks_ttl_s
        self._min_refetch_s = settings.ticket_jwks_min_refetch_s
        self._clock = clock
        self._http = httpx.Client(timeout=settings.ticket_jwks_timeout_s, transport=transport)
        self._lock = threading.Lock()
        self._keys: dict[str, Ed25519PublicKey] = {}
        self._fetched_at: float | None = None
        self._attempted_at: float | None = None
        self._last_fetch_failed = False

    def key(self, kid: str) -> Ed25519PublicKey | None:
        """The key with this kid, or None when the published set doesn't have it (an invalid ticket)."""
        with self._lock:
            now = self._clock()
            stale = self._fetched_at is None or now - self._fetched_at >= self._ttl_s
            if (stale or kid not in self._keys) and self._may_fetch(now):
                self._refresh(now)
            if self._fetched_at is None or (kid not in self._keys and self._last_fetch_failed):
                raise TicketsUnavailableError("Check-in is unavailable: the ticket keys could not be fetched")
            return self._keys.get(kid)

    def _may_fetch(self, now: float) -> bool:
        return self._attempted_at is None or now - self._attempted_at >= self._min_refetch_s

    def _refresh(self, now: float) -> None:
        self._attempted_at = now
        try:
            response = self._http.get(self._url, headers={"Accept": "application/jwk-set+json, application/json"})
            response.raise_for_status()
            keys = self._parse(response.json())
        except (httpx.HTTPError, ValueError, jwt.PyJWTError) as exc:
            log.warning("ticket_keys.fetch_failed url=%s keeping=%s error=%s", self._url, len(self._keys), exc)
            self._last_fetch_failed = True
            return
        self._last_fetch_failed = False
        added = keys.keys() - self._keys.keys()
        removed = self._keys.keys() - keys.keys()
        if added or removed:
            log.info("ticket_keys.updated kids=%s added=%s removed=%s", sorted(keys), sorted(added), sorted(removed))
        self._keys = keys
        self._fetched_at = now

    @staticmethod
    def _parse(document: object) -> dict[str, Ed25519PublicKey]:
        if not isinstance(document, dict) or not isinstance(document.get("keys"), list):
            raise ValueError("not a JWK set")
        keys: dict[str, Ed25519PublicKey] = {}
        for entry in document["keys"]:
            if not isinstance(entry, dict) or entry.get("kty") != "OKP" or entry.get("crv") != "Ed25519":
                continue
            if entry.get("alg", "EdDSA") != "EdDSA" or entry.get("use", "sig") != "sig" or not entry.get("kid"):
                continue
            key = jwt.PyJWK(entry).key
            if isinstance(key, Ed25519PublicKey):
                keys[str(entry["kid"])] = key
        if not keys:
            raise ValueError("the JWK set has no Ed25519 signing keys")
        return keys


@lru_cache
def get_ticket_keys() -> TicketKeySet:
    """One cache per process, shared by every request thread."""
    return TicketKeySet(get_settings())
