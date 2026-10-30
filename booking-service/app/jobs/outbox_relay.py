"""Outbox relay process: python -m app.jobs.outbox_relay [--once]

Publishes committed outbox rows to the smarttours.events exchange (see app.services.outbox_relay).
A full batch is followed straight away by the next one; otherwise it waits OUTBOX_POLL_INTERVAL_S.
Every OUTBOX_PRUNE_INTERVAL_S it deletes rows published longer than OUTBOX_RETENTION_HOURS ago.
Safe to run several copies. Stops cleanly on SIGTERM/SIGINT, between batches.
"""

import argparse
import logging
import signal
import threading
import time
from datetime import timedelta

from ..config import get_settings
from ..database import SessionLocal, engine
from ..messaging import RabbitPublisher
from ..services.outbox_relay import prune_published, relay_batch

log = logging.getLogger("app.jobs.outbox_relay")

MAX_BACKOFF_S = 30.0


def main() -> None:
    parser = argparse.ArgumentParser(description="Relay outbox events to RabbitMQ")
    parser.add_argument("--once", action="store_true", help="publish everything pending once, then exit")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")

    settings = get_settings()
    publisher = RabbitPublisher(settings.rabbitmq_url, settings.events_exchange)
    retention = timedelta(hours=settings.outbox_retention_hours)

    stop = threading.Event()
    for sig in (signal.SIGTERM, signal.SIGINT):
        signal.signal(sig, lambda *_: stop.set())

    log.info("outbox relay started, batch=%s poll=%ss", settings.outbox_batch_size, settings.outbox_poll_interval_s)
    backoff = 1.0
    next_prune = 0.0
    try:
        while not stop.is_set():
            try:
                published = relay_batch(SessionLocal, publisher, settings.outbox_batch_size)
                if time.monotonic() >= next_prune:
                    pruned = prune_published(SessionLocal, retention, settings.outbox_prune_batch_size)
                    if pruned:
                        log.info("outbox.pruned count=%s", pruned)
                    next_prune = time.monotonic() + settings.outbox_prune_interval_s
                backoff = 1.0
            except Exception:
                # Broker or database outage: nothing was marked, so nothing is lost. Back off and retry.
                if args.once:
                    raise
                log.exception("outbox relay batch failed; retrying in %.0fs", backoff)
                stop.wait(backoff)
                backoff = min(backoff * 2, MAX_BACKOFF_S)
                continue
            if args.once and published < settings.outbox_batch_size:
                break
            if published < settings.outbox_batch_size:
                stop.wait(settings.outbox_poll_interval_s)
    finally:
        publisher.close()
        engine.dispose()
        log.info("outbox relay stopped")


if __name__ == "__main__":
    main()
