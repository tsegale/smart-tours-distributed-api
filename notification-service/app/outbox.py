"""ticket.rendered through a transactional outbox: the same table, envelope and relay design as booking-service
(ADR 0008), so a ticket is never announced before it is recorded and never lost if the broker is down.

The relay runs as a Celery task: right after a ticket is recorded (low latency) and every outbox_relay_interval_s
from Beat (the backstop). One batch per transaction: claim FOR UPDATE SKIP LOCKED, publish with confirms, mark.
"""

import json
import logging
import uuid
from datetime import UTC, datetime
from typing import Any

import pika
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from .config import Settings
from .models import OutboxEvent, Ticket

log = logging.getLogger(__name__)

TICKET_RENDERED = "ticket.rendered"
TICKET_SCHEMA_VERSION = 1


def record(
    db: Session, *, aggregate_type: str, aggregate_id: object, event_type: str, data: dict[str, Any]
) -> uuid.UUID:
    event_id = uuid.uuid4()
    occurred_at = datetime.now(UTC)
    payload = {"eventId": str(event_id), "eventType": event_type, "occurredAt": occurred_at.isoformat(), **data}
    db.add(
        OutboxEvent(
            id=event_id,
            aggregate_type=aggregate_type,
            aggregate_id=str(aggregate_id),
            event_type=event_type,
            payload=payload,
            occurred_at=occurred_at,
        )
    )
    return event_id


def ticket_rendered(db: Session, ticket: Ticket) -> uuid.UUID:
    return record(
        db,
        aggregate_type="ticket",
        aggregate_id=ticket.booking_id,
        event_type=TICKET_RENDERED,
        data={
            "schemaVersion": TICKET_SCHEMA_VERSION,
            "bookingId": ticket.booking_id,
            "reference": ticket.reference,
            "bucket": ticket.bucket,
            "objectKey": ticket.object_key,
            "contentType": "application/pdf",
            "sizeBytes": ticket.size_bytes,
            "sha256": ticket.sha256,
        },
    )


class PublishNotConfirmedError(Exception):
    """The broker did not confirm a message. Nothing in the batch is marked."""


def relay_batch(db: Session, settings: Settings) -> int:
    """Publish one batch of unpublished events with publisher confirms. Returns how many were published."""
    rows = list(
        db.execute(
            select(OutboxEvent)
            .where(OutboxEvent.published_at.is_(None))
            .order_by(OutboxEvent.occurred_at, OutboxEvent.id)
            .limit(settings.outbox_batch_size)
            .with_for_update(skip_locked=True)
        ).scalars()
    )
    if not rows:
        db.rollback()
        return 0
    try:
        connection = pika.BlockingConnection(pika.URLParameters(settings.rabbitmq_url))
    except pika.exceptions.AMQPError as exc:
        db.rollback()
        raise PublishNotConfirmedError(f"broker unreachable: {exc.__class__.__name__}") from exc
    try:
        channel = connection.channel()
        channel.exchange_declare(settings.events_exchange, exchange_type="topic", durable=True)
        channel.confirm_delivery()
        for row in rows:
            channel.basic_publish(
                exchange=settings.events_exchange,
                routing_key=row.event_type,
                body=json.dumps(row.payload, separators=(",", ":")).encode(),
                properties=pika.BasicProperties(
                    content_type="application/json",
                    delivery_mode=pika.DeliveryMode.Persistent,
                    message_id=str(row.id),
                    type=row.event_type,
                    timestamp=int(row.occurred_at.timestamp()),
                ),
            )
    except pika.exceptions.AMQPError as exc:
        db.rollback()
        raise PublishNotConfirmedError(f"publish failed: {exc.__class__.__name__}: {exc}") from exc
    finally:
        if connection.is_open:
            connection.close()
    db.execute(
        update(OutboxEvent)
        .where(OutboxEvent.id.in_([r.id for r in rows]))
        .values(published_at=datetime.now(UTC))
        .execution_options(synchronize_session=False)
    )
    db.commit()
    log.info("outbox.published count=%s", len(rows))
    return len(rows)
