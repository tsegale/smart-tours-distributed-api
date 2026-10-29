"""Synchronous client for tour-core-service, used to validate tours and snapshot their price.

Called directly on the internal network (not through the gateway). Failure handling:
- connect/read timeouts on every call, so a hung tour-core can't hold a request thread forever;
- bounded retries with exponential backoff, only for timeouts, connection errors and 5xx,
  which are safe to retry because GET is idempotent;
- 404 maps to a tour-not-found Problem, anything else that's wrong maps to 503.
"""

import logging
import random
import time
from dataclasses import dataclass
from decimal import Decimal

import httpx

from ..config import Settings, get_settings
from ..domain.money import to_amount
from ..errors import TourNotFoundError, UpstreamUnavailableError

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class TourSnapshot:
    id: int
    name: str
    price: Decimal
    currency: str


class TourClient:
    def __init__(self, settings: Settings, transport: httpx.BaseTransport | None = None) -> None:
        self._max_retries = settings.tour_service_max_retries
        self._backoff_s = settings.tour_service_backoff_s
        self._http = httpx.Client(
            base_url=settings.tour_service_url,
            timeout=httpx.Timeout(
                connect=settings.tour_service_connect_timeout_s,
                read=settings.tour_service_read_timeout_s,
                write=settings.tour_service_read_timeout_s,
                pool=settings.tour_service_connect_timeout_s,
            ),
            headers={"Accept": "application/json"},
            transport=transport,
        )

    def get_tour(self, tour_id: int) -> TourSnapshot:
        response = self._get_with_retries(f"/api/tours/{tour_id}")
        if response.status_code == 404:
            raise TourNotFoundError(f"Tour {tour_id} does not exist", tour_id=tour_id)
        if response.status_code != 200:
            log.warning("tour-core returned %s for tour %s", response.status_code, tour_id)
            raise UpstreamUnavailableError(
                "The tour catalogue returned an unexpected response", retryable=True, upstream="tour-core-service"
            )
        try:
            body = response.json(parse_float=Decimal)  # exact prices, never via float
            return TourSnapshot(
                id=int(body["id"]),
                name=str(body["name"]),
                price=to_amount(body["price"]),
                currency=str(body.get("currency") or "NAD").upper(),
            )
        except (ValueError, KeyError, TypeError) as exc:
            log.error("tour-core returned an unreadable tour %s: %s", tour_id, exc)
            raise UpstreamUnavailableError(
                "The tour catalogue returned an unreadable tour", upstream="tour-core-service"
            ) from exc

    def _get_with_retries(self, path: str) -> httpx.Response:
        attempts = self._max_retries + 1
        for attempt in range(1, attempts + 1):
            try:
                response = self._http.get(path)
            except (httpx.TimeoutException, httpx.TransportError) as exc:
                failure = f"{type(exc).__name__}: {exc}"
            else:
                if response.status_code < 500:
                    return response
                failure = f"HTTP {response.status_code}"
            if attempt == attempts:
                log.error("tour-core unavailable after %s attempts (%s) for %s", attempts, failure, path)
                raise UpstreamUnavailableError(
                    "The tour catalogue is unavailable; please retry", retryable=True, upstream="tour-core-service"
                )
            delay = self._backoff_s * 2 ** (attempt - 1)
            log.warning("tour-core attempt %s/%s failed (%s); retrying in %.2fs", attempt, attempts, failure, delay)
            time.sleep(delay * random.uniform(0.5, 1.0))  # jitter: don't retry in lockstep with other requests
        raise AssertionError("unreachable")

    def close(self) -> None:
        self._http.close()


_client: TourClient | None = None


def get_tour_client() -> TourClient:
    """FastAPI dependency. One pooled client per process."""
    global _client
    if _client is None:
        _client = TourClient(get_settings())
    return _client


def close_tour_client() -> None:
    global _client
    if _client is not None:
        _client.close()
        _client = None
