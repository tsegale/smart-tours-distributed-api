"""Moves committed outbox rows to RabbitMQ: at-least-once, never for a change that rolled back.

One batch, in one transaction:
    1. SELECT the oldest unpublished rows FOR UPDATE SKIP LOCKED, LIMIT batch.
    2. Publish them and wait for the broker's publisher confirms.
    3. Set published_at on exactly those rows, commit.

- Two relays never publish the same batch: the second one skips the rows the first has locked
  and takes the next ones (tests/test_outbox_relay.py runs two at once).
- A crash after step 2 but before step 3 rolls back the claim, so the rows are published again by
  the next run: a duplicate, never a loss. Consumers dedupe by eventId or by version.
- A failed publish (broker down, a nack) raises before step 3: nothing is marked and the batch is
  retried on the next run.

Order across batches is not guaranteed once several relays run (a batch can be overtaken by the
next one). availability.changed carries a version for exactly that reason.
"""

import logging
import uuid
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Protocol

from sqlalchemy import delete, func, select, update
from sqlalchemy.orm import Session

from ..models import OutboxEvent

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class OutboundMessage:
    event_id: uuid.UUID
    routing_key: str
    payload: dict[str, Any]
    occurred_at: datetime


class Publisher(Protocol):
    def publish(self, messages: Sequence[OutboundMessage]) -> None:
        """Return only once the broker has confirmed every message; raise otherwise."""
        ...


def relay_batch(session_factory: Callable[[], Session], publisher: Publisher, batch_size: int) -> int:
    """Publish one batch of unpublished events. Returns how many were published (0 when idle)."""
    with session_factory() as db:
        try:
            rows = list(
                db.execute(
                    select(OutboxEvent)
                    .where(OutboxEvent.published_at.is_(None))
                    .order_by(OutboxEvent.occurred_at, OutboxEvent.id)
                    .limit(batch_size)
                    .with_for_update(skip_locked=True)
                ).scalars()
            )
            if not rows:
                db.rollback()
                return 0
            publisher.publish([OutboundMessage(r.id, r.event_type, r.payload, r.occurred_at) for r in rows])
            db.execute(
                update(OutboxEvent)
                .where(OutboxEvent.id.in_([r.id for r in rows]))
                .values(published_at=func.now())
                .execution_options(synchronize_session=False)
            )
            db.commit()
        except Exception:
            db.rollback()
            raise
    log.debug("outbox.published count=%s", len(rows))
    return len(rows)


def prune_published(
    session_factory: Callable[[], Session], retention: timedelta, batch_size: int, now: datetime | None = None
) -> int:
    """Delete published rows older than `retention`, in batches so no single statement runs long."""
    cutoff = (now or datetime.now(UTC)) - retention
    deleted = 0
    while True:
        with session_factory() as db:
            try:
                doomed = (
                    select(OutboxEvent.id).where(OutboxEvent.published_at < cutoff).limit(batch_size).scalar_subquery()
                )
                count = db.execute(
                    delete(OutboxEvent).where(OutboxEvent.id.in_(doomed)).execution_options(synchronize_session=False)
                ).rowcount
                db.commit()
            except Exception:
                db.rollback()
                raise
        deleted += count
        if count < batch_size:
            return deleted
