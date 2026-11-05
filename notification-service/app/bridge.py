"""Event bridge: booking lifecycle events in, Celery tasks out. `python -m app.bridge`

Domain events stay on RabbitMQ's default vhost and the consumer topology of ADR 0005; Celery's task messages live
in their own vhost. The bridge is the only thing that crosses: it records each event idempotently in one
transaction, commits, then enqueues the tasks (app.dispatch), and only then acks.

    smarttours.events --booking.*--> notifications.booking-events --(fails)--> notifications.booking-events.retry
                                            ^                                     | TTL expires
                                            +-------------------------------------+
    after max_retries, or at once for a poison message:
    notifications.booking-events --reject--> .dlx --> notifications.booking-events.dlq
"""

import enum
import logging
import signal
import time
from collections.abc import Callable

import pika
from opentelemetry.instrumentation.celery import CeleryInstrumentor
from pika.adapters.blocking_connection import BlockingChannel
from pika.exceptions import AMQPConnectionError, ChannelClosedByBroker

from .config import Settings, get_settings
from .database import SessionLocal
from .dispatch import dispatch
from .events import ROUTING_KEYS, PoisonMessageError, parse_booking_event
from .intake import record_booking_event
from .logs import configure_logging
from .telemetry import configure_telemetry

log = logging.getLogger("notifications.bridge")

RETRY_HEADER = "x-retry-count"


class Action(enum.Enum):
    ACK = "ack"
    RETRY = "retry"
    DEAD_LETTER = "dead_letter"


def handle(body: bytes) -> None:
    """Record the event and enqueue what it owes. Raises on anything that should be retried."""
    event = parse_booking_event(body)
    with SessionLocal() as db:
        try:
            created = record_booking_event(db, event)
            db.commit()
        except Exception:
            db.rollback()
            raise
    log.info(
        "event.recorded event_id=%s booking_id=%s type=%s notifications=%s",
        event.event_id,
        event.booking_id,
        event.event_type,
        created,
        extra={"event_id": str(event.event_id), "booking_id": event.booking_id},
    )
    dispatch(SessionLocal, created)


def decide(body: bytes, headers: dict | None, handler: Callable[[bytes], object], max_retries: int) -> Action:
    attempt = int((headers or {}).get(RETRY_HEADER, 0))
    try:
        handler(body)
        return Action.ACK
    except PoisonMessageError:
        log.exception("Poison message, dead-lettering without retry")
        return Action.DEAD_LETTER
    except Exception:
        if attempt < max_retries:
            log.exception("Handler failed (attempt %d of %d), scheduling retry", attempt + 1, max_retries + 1)
            return Action.RETRY
        log.exception("Handler failed %d times, dead-lettering", attempt + 1)
        return Action.DEAD_LETTER


def apply(action: Action, channel, method, properties: pika.BasicProperties, body: bytes, settings: Settings) -> None:
    if action is Action.ACK:
        channel.basic_ack(delivery_tag=method.delivery_tag)
    elif action is Action.RETRY:
        headers = dict(properties.headers or {})
        headers[RETRY_HEADER] = int(headers.get(RETRY_HEADER, 0)) + 1
        channel.basic_publish(
            exchange="",
            routing_key=retry_queue(settings),
            body=body,
            properties=pika.BasicProperties(
                content_type=properties.content_type,
                delivery_mode=pika.DeliveryMode.Persistent,
                message_id=properties.message_id,
                type=properties.type,
                headers=headers,
            ),
        )
        # Ack only after the retry copy is published: a crash in between is a duplicate, which the
        # processed_events table absorbs, never a loss.
        channel.basic_ack(delivery_tag=method.delivery_tag)
    else:
        channel.basic_reject(delivery_tag=method.delivery_tag, requeue=False)


def retry_queue(settings: Settings) -> str:
    return f"{settings.queue_name}.retry"


def declare_topology(channel: BlockingChannel, settings: Settings) -> None:
    dlx = f"{settings.queue_name}.dlx"
    dlq = f"{settings.queue_name}.dlq"
    quorum = {"x-queue-type": "quorum"}

    channel.exchange_declare(settings.events_exchange, exchange_type="topic", durable=True)
    channel.exchange_declare(dlx, exchange_type="fanout", durable=True)
    channel.queue_declare(dlq, durable=True, arguments=quorum)
    channel.queue_bind(dlq, dlx)
    channel.queue_declare(settings.queue_name, durable=True, arguments={**quorum, "x-dead-letter-exchange": dlx})
    for key in ROUTING_KEYS:
        channel.queue_bind(settings.queue_name, settings.events_exchange, routing_key=key)
    channel.queue_declare(
        retry_queue(settings),
        durable=True,
        arguments={
            **quorum,
            "x-message-ttl": settings.retry_delay_ms,
            "x-dead-letter-exchange": "",
            "x-dead-letter-routing-key": settings.queue_name,
        },
    )


def run() -> None:
    configure_logging("notification-bridge")
    configure_telemetry("notification-bridge")
    CeleryInstrumentor().instrument()  # the tasks it enqueues carry the trace in their headers
    settings = get_settings()
    stopping = False
    backoff = 1.0
    while not stopping:
        connection = None
        try:
            connection = pika.BlockingConnection(pika.URLParameters(settings.rabbitmq_url))
            channel = connection.channel()
            declare_topology(channel, settings)
            channel.basic_qos(prefetch_count=settings.prefetch)

            def on_message(ch, method, properties, body):
                apply(decide(body, properties.headers, handle, settings.max_retries), ch, method, properties, body,
                      settings)  # fmt: skip

            channel.basic_consume(settings.queue_name, on_message_callback=on_message)

            def shutdown(*_, conn=connection, ch=channel):
                nonlocal stopping
                stopping = True
                conn.add_callback_threadsafe(ch.stop_consuming)

            signal.signal(signal.SIGTERM, shutdown)
            signal.signal(signal.SIGINT, shutdown)
            log.info("Consuming %s (routing keys %s)", settings.queue_name, ", ".join(ROUTING_KEYS))
            backoff = 1.0
            channel.start_consuming()
        except (AMQPConnectionError, ChannelClosedByBroker) as exc:
            log.warning("Broker unavailable (%s), reconnecting in %.0fs", exc.__class__.__name__, backoff)
            time.sleep(backoff)
            backoff = min(backoff * 2, 30.0)
        finally:
            if connection is not None and connection.is_open:
                connection.close()
    log.info("Bridge stopped")


if __name__ == "__main__":
    run()
