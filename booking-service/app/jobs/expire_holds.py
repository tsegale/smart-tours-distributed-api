"""Hold sweeper process: python -m app.jobs.expire_holds [--once]

Every SWEEPER_INTERVAL_S seconds (default 60) it expires overdue PENDING_PAYMENT holds and
retries refunds left pending. Safe to run several copies: see app.services.sweeper.
Stops cleanly on SIGTERM/SIGINT, between runs.
"""

import argparse
import logging
import signal
import threading

from ..config import get_settings
from ..database import SessionLocal, engine
from ..payments import get_payment_provider
from ..services.sweeper import expire_overdue_holds, retry_pending_refunds

log = logging.getLogger("app.jobs.expire_holds")


def run_once() -> None:
    settings = get_settings()
    result = expire_overdue_holds(SessionLocal, settings.sweeper_batch_size)
    refunded = retry_pending_refunds(SessionLocal, get_payment_provider(), settings.sweeper_batch_size)
    if result.expired or result.departures_skipped or refunded:
        log.info(
            "sweep expired=%s seats_released=%s departures_skipped=%s refunds_retried=%s",
            result.expired,
            result.seats_released,
            result.departures_skipped,
            refunded,
        )


def main() -> None:
    parser = argparse.ArgumentParser(description="Expire unpaid seat holds")
    parser.add_argument("--once", action="store_true", help="run one sweep and exit")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")

    stop = threading.Event()
    for sig in (signal.SIGTERM, signal.SIGINT):
        signal.signal(sig, lambda *_: stop.set())

    interval = get_settings().sweeper_interval_s
    log.info("hold sweeper started, interval=%ss", interval)
    try:
        while not stop.is_set():
            try:
                run_once()
            except Exception:
                # One failed run (database restart, provider outage) must not kill the process.
                log.exception("sweep failed; retrying next interval")
                if args.once:
                    raise
            if args.once:
                break
            stop.wait(interval)
    finally:
        engine.dispose()
        log.info("hold sweeper stopped")


if __name__ == "__main__":
    main()
