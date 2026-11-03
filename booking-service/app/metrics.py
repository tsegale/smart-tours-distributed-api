"""Prometheus metrics for scraping on the internal network (the gateway routes no /metrics path here).

Relay lag of the transactional outbox, read from the table at scrape time, so every uvicorn worker and the
relay process itself agree without prometheus_client's multiprocess mode:

    outbox_oldest_unpublished_age_seconds   how long the oldest unpublished event has waited; 0 when drained
    outbox_unpublished_events               the backlog

tour-core exports the same two gauges for its outbox (ADR 0013), so one dashboard and one alert cover both.
"""

import logging
import math
from collections.abc import Iterator

from prometheus_client import CONTENT_TYPE_LATEST, CollectorRegistry, generate_latest
from prometheus_client.core import GaugeMetricFamily
from prometheus_client.registry import Collector
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from .database import engine

log = logging.getLogger(__name__)

OUTBOX_LAG_SQL = text(
    "SELECT COALESCE(EXTRACT(EPOCH FROM now() - min(occurred_at)), 0), count(*) FROM outbox WHERE published_at IS NULL"
)


class OutboxCollector(Collector):
    def collect(self) -> Iterator[GaugeMetricFamily]:
        try:
            with engine.connect() as conn:
                age, pending = conn.execute(OUTBOX_LAG_SQL).one()
            age, pending = float(age), float(pending)
        except SQLAlchemyError:
            # NaN, not 0: a scrape must not report a healthy outbox it could not read.
            log.warning("outbox metrics unavailable", exc_info=True)
            age = pending = math.nan
        yield GaugeMetricFamily(
            "outbox_oldest_unpublished_age_seconds",
            "Age of the oldest event not yet confirmed by the broker; 0 when there is none",
            value=age,
        )
        yield GaugeMetricFamily(
            "outbox_unpublished_events", "Events written but not yet confirmed by the broker", value=pending
        )


registry = CollectorRegistry()
registry.register(OutboxCollector())


def render() -> tuple[bytes, str]:
    return generate_latest(registry), CONTENT_TYPE_LATEST
