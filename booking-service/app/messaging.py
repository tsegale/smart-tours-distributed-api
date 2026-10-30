"""RabbitMQ publisher for the outbox relay: persistent messages with publisher confirms.

The producer declares only the topic exchange it publishes to (ADR 0005). Consumers own their
queues, bindings, retry queues and DLQs.
"""

import json
import logging
from collections.abc import Sequence

import pika
from pika.adapters.blocking_connection import BlockingChannel, BlockingConnection

from .services.outbox_relay import OutboundMessage

log = logging.getLogger(__name__)


class PublishNotConfirmedError(Exception):
    """The broker did not confirm a message (nack or connection lost). Nothing in the batch is marked."""


class RabbitPublisher:
    """Publishes over one long-lived connection, reopened lazily after any failure."""

    def __init__(self, url: str, exchange: str) -> None:
        self._parameters = pika.URLParameters(url)
        self._exchange = exchange
        self._connection: BlockingConnection | None = None
        self._channel: BlockingChannel | None = None

    def _ensure_channel(self) -> BlockingChannel:
        if self._channel is None or not self._channel.is_open:
            self.close()
            self._connection = pika.BlockingConnection(self._parameters)
            channel = self._connection.channel()
            channel.exchange_declare(self._exchange, exchange_type="topic", durable=True)
            # basic_publish now blocks until the broker acks the message, and raises on a nack.
            channel.confirm_delivery()
            self._channel = channel
        return self._channel

    def publish(self, messages: Sequence[OutboundMessage]) -> None:
        try:
            channel = self._ensure_channel()
            for message in messages:
                channel.basic_publish(
                    exchange=self._exchange,
                    routing_key=message.routing_key,
                    body=json.dumps(message.payload, separators=(",", ":")).encode(),
                    properties=pika.BasicProperties(
                        content_type="application/json",
                        delivery_mode=pika.DeliveryMode.Persistent,
                        message_id=str(message.event_id),
                        type=message.routing_key,
                        timestamp=int(message.occurred_at.timestamp()),
                    ),
                )
        except pika.exceptions.AMQPError as exc:
            # Unconfirmed: drop the connection so the next batch starts on a fresh one.
            self.close()
            raise PublishNotConfirmedError(f"publish failed: {exc.__class__.__name__}: {exc}") from exc

    def close(self) -> None:
        connection, self._connection, self._channel = self._connection, None, None
        if connection is not None and connection.is_open:
            try:
                connection.close()
            except pika.exceptions.AMQPError:
                log.warning("error closing the RabbitMQ connection", exc_info=True)
