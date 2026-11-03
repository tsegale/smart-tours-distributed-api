"""Ticket listener: python -m app.jobs.ticket_listener

Records ticket.rendered events from notification-service, so GET /api/bookings/{id}/ticket knows where the PDF is
without reading another service's database (ADR 0001, ADR 0015). Consumer topology of ADR 0005:

    smarttours.events --ticket.rendered--> booking.tickets --(fails)--> booking.tickets.retry --TTL--> booking.tickets
    after max retries, or at once for a malformed message: booking.tickets --reject--> .dlx --> booking.tickets.dlq

Idempotent: a duplicate or older event changes nothing (app.services.tickets.record_ticket_rendered).
"""

import enum
import json
import logging
import signal
import time

import pika
from pika.adapters.blocking_connection import BlockingChannel
from pika.exceptions import AMQPConnectionError, ChannelClosedByBroker

from ..config import Settings, get_settings
from ..database import SessionLocal
from ..services.tickets import record_ticket_rendered

log = logging.getLogger("app.jobs.ticket_listener")

ROUTING_KEY = "ticket.rendered"
RETRY_HEADER = "x-retry-count"


class Action(enum.Enum):
    ACK = "ack"
    RETRY = "retry"
    DEAD_LETTER = "dead_letter"


class MalformedEventError(Exception):
    """Never processable: dead-letter it without retrying."""


def handle(body: bytes) -> bool:
    try:
        event = json.loads(body)
        if event.get("eventType") != ROUTING_KEY:
            raise MalformedEventError(f"not a ticket.rendered event: {event.get('eventType')}")
    except ValueError as exc:
        raise MalformedEventError(f"unreadable event: {exc}") from exc
    with SessionLocal() as db:
        try:
            recorded = record_ticket_rendered(db, event)
        except (KeyError, TypeError, ValueError) as exc:
            db.rollback()
            raise MalformedEventError(f"ticket.rendered without the documented fields: {exc}") from exc
        except Exception:
            db.rollback()
            raise
        db.commit()
    log.info("ticket.recorded booking_id=%s applied=%s", event.get("bookingId"), recorded)
    return recorded


def decide(body: bytes, headers: dict | None, max_retries: int) -> Action:
    attempt = int((headers or {}).get(RETRY_HEADER, 0))
    try:
        handle(body)
        return Action.ACK
    except MalformedEventError:
        log.exception("Malformed ticket event, dead-lettering without retry")
        return Action.DEAD_LETTER
    except Exception:
        if attempt < max_retries:
            log.exception("Recording the ticket failed (attempt %d of %d), retrying", attempt + 1, max_retries + 1)
            return Action.RETRY
        log.exception("Recording the ticket failed %d times, dead-lettering", attempt + 1)
        return Action.DEAD_LETTER


def apply(action: Action, channel, method, properties: pika.BasicProperties, body: bytes, settings: Settings) -> None:
    if action is Action.ACK:
        channel.basic_ack(delivery_tag=method.delivery_tag)
    elif action is Action.RETRY:
        headers = dict(properties.headers or {})
        headers[RETRY_HEADER] = int(headers.get(RETRY_HEADER, 0)) + 1
        channel.basic_publish(
            exchange="",
            routing_key=f"{settings.ticket_queue_name}.retry",
            body=body,
            properties=pika.BasicProperties(
                content_type=properties.content_type,
                delivery_mode=pika.DeliveryMode.Persistent,
                message_id=properties.message_id,
                headers=headers,
            ),
        )
        channel.basic_ack(delivery_tag=method.delivery_tag)
    else:
        channel.basic_reject(delivery_tag=method.delivery_tag, requeue=False)


def declare_topology(channel: BlockingChannel, settings: Settings) -> None:
    queue = settings.ticket_queue_name
    quorum = {"x-queue-type": "quorum"}
    channel.exchange_declare(settings.events_exchange, exchange_type="topic", durable=True)
    channel.exchange_declare(f"{queue}.dlx", exchange_type="fanout", durable=True)
    channel.queue_declare(f"{queue}.dlq", durable=True, arguments=quorum)
    channel.queue_bind(f"{queue}.dlq", f"{queue}.dlx")
    channel.queue_declare(queue, durable=True, arguments={**quorum, "x-dead-letter-exchange": f"{queue}.dlx"})
    channel.queue_bind(queue, settings.events_exchange, routing_key=ROUTING_KEY)
    channel.queue_declare(
        f"{queue}.retry",
        durable=True,
        arguments={
            **quorum,
            "x-message-ttl": settings.ticket_retry_delay_ms,
            "x-dead-letter-exchange": "",
            "x-dead-letter-routing-key": queue,
        },
    )


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    settings = get_settings()
    stopping = False
    backoff = 1.0
    while not stopping:
        connection = None
        try:
            connection = pika.BlockingConnection(pika.URLParameters(settings.rabbitmq_url))
            channel = connection.channel()
            declare_topology(channel, settings)
            channel.basic_qos(prefetch_count=10)

            def on_message(ch, method, properties, body):
                apply(decide(body, properties.headers, settings.ticket_max_retries), ch, method, properties, body,
                      settings)  # fmt: skip

            channel.basic_consume(settings.ticket_queue_name, on_message_callback=on_message)

            def shutdown(*_, conn=connection, ch=channel):
                nonlocal stopping
                stopping = True
                conn.add_callback_threadsafe(ch.stop_consuming)

            signal.signal(signal.SIGTERM, shutdown)
            signal.signal(signal.SIGINT, shutdown)
            log.info("Consuming %s (%s)", settings.ticket_queue_name, ROUTING_KEY)
            backoff = 1.0
            channel.start_consuming()
        except (AMQPConnectionError, ChannelClosedByBroker) as exc:
            log.warning("Broker unavailable (%s), reconnecting in %.0fs", exc.__class__.__name__, backoff)
            time.sleep(backoff)
            backoff = min(backoff * 2, 30.0)
        finally:
            if connection is not None and connection.is_open:
                connection.close()
    log.info("Ticket listener stopped")


if __name__ == "__main__":
    main()
