"""RabbitMQ consumer: keeps tour embeddings in sync with tour-core-service.

Runs as its own process (`python -m app.worker`), separate from the HTTP API, so heavy
embedding work never competes with request handling and the two can scale separately.

Failure handling (the original consumer requeued forever, so one bad message blocked the queue):

    smarttours.events --tour.*--> ai.tour-embedding --(handler fails)--> ai.tour-embedding.retry
                                        ^                                  | TTL expires
                                        +----------------------------------+
    after max_retries, or immediately for malformed messages:
    ai.tour-embedding --reject--> ai.tour-embedding.dlx --> ai.tour-embedding.dlq (inspect / replay)
"""

import enum
import logging
import signal
import time
from collections.abc import Callable

import pika
from pika.adapters.blocking_connection import BlockingChannel
from pika.exceptions import AMQPConnectionError, ChannelClosedByBroker

from .config import Settings, get_settings
from .database import SessionLocal
from .embeddings import get_embedder
from .messages import PoisonMessageError, handle_tour_event

log = logging.getLogger("ai.worker")

RETRY_HEADER = "x-retry-count"


class Action(enum.Enum):
    ACK = "ack"
    RETRY = "retry"
    DEAD_LETTER = "dead_letter"


def decide(body: bytes, headers: dict | None, handler: Callable[[bytes], object], max_retries: int) -> Action:
    """Run the handler and decide what to do with the message. Pure logic, unit tested."""
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
                headers=headers,
            ),
        )
        # Ack only after the retry copy is published. A crash in between means a duplicate,
        # never a loss, and the handler is idempotent (an upsert keyed on tour_id).
        channel.basic_ack(delivery_tag=method.delivery_tag)
    else:
        channel.basic_reject(delivery_tag=method.delivery_tag, requeue=False)  # -> DLX -> DLQ


def retry_queue(settings: Settings) -> str:
    return f"{settings.queue_name}.retry"


def declare_topology(channel: BlockingChannel, settings: Settings) -> None:
    dlx = f"{settings.queue_name}.dlx"
    dlq = f"{settings.queue_name}.dlq"
    quorum = {"x-queue-type": "quorum"}  # replicated, durable queues (RabbitMQ's recommended default)

    channel.exchange_declare(settings.events_exchange, exchange_type="topic", durable=True)
    channel.exchange_declare(dlx, exchange_type="fanout", durable=True)

    channel.queue_declare(dlq, durable=True, arguments=quorum)
    channel.queue_bind(dlq, dlx)

    channel.queue_declare(settings.queue_name, durable=True, arguments={**quorum, "x-dead-letter-exchange": dlx})
    for key in settings.routing_keys:
        channel.queue_bind(settings.queue_name, settings.events_exchange, routing_key=key)

    # Messages wait here for retry_delay_ms, then dead-letter back into the main queue.
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
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    settings = get_settings()
    embedder = get_embedder()
    if hasattr(embedder, "warm_up"):
        # Load the model before consuming, so the first event after a deploy isn't held up by it.
        embedder.warm_up()

    def handler(body: bytes) -> object:
        return handle_tour_event(body, SessionLocal, embedder)

    stopping = False
    backoff = 1.0

    while not stopping:
        connection = None
        try:
            connection = pika.BlockingConnection(pika.URLParameters(settings.rabbitmq_url))
            channel = connection.channel()
            declare_topology(channel, settings)
            channel.basic_qos(prefetch_count=1)  # embeddings are CPU-heavy: one at a time per worker

            def on_message(ch, method, properties, body):
                apply(
                    decide(body, properties.headers, handler, settings.max_retries),
                    ch,
                    method,
                    properties,
                    body,
                    settings,
                )

            channel.basic_consume(settings.queue_name, on_message_callback=on_message)

            def shutdown(*_, conn=connection, ch=channel):
                # Finish the in-flight message, then stop: no half-processed deliveries on deploy.
                nonlocal stopping
                stopping = True
                conn.add_callback_threadsafe(ch.stop_consuming)

            signal.signal(signal.SIGTERM, shutdown)
            signal.signal(signal.SIGINT, shutdown)

            log.info("Consuming %s (routing keys %s)", settings.queue_name, ", ".join(settings.routing_keys))
            backoff = 1.0
            channel.start_consuming()
        except (AMQPConnectionError, ChannelClosedByBroker) as exc:
            log.warning("Broker unavailable (%s), reconnecting in %.0fs", exc.__class__.__name__, backoff)
            time.sleep(backoff)
            backoff = min(backoff * 2, 30.0)
        finally:
            if connection is not None and connection.is_open:
                connection.close()
    log.info("Worker stopped")


if __name__ == "__main__":
    run()
