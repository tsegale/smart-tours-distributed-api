"""A trace survives the outbox: the row keeps the writer's traceparent and the relay publishes inside it (ADR 0017)."""

import json
import logging
import os
import uuid

import pytest
from opentelemetry.sdk.trace import TracerProvider
from sqlalchemy import select

from app.database import SessionLocal
from app.models import OutboxEvent
from app.services.outbox_relay import relay_batch
from app.telemetry import JsonFormatter, _TraceContext, continuing, current_traceparent

RABBITMQ_URL = os.environ.get("TEST_RABBITMQ_URL")
tracer = TracerProvider().get_tracer("test")


def newest_event(availability_id: int) -> OutboxEvent:
    with SessionLocal() as db:
        return (
            db.execute(
                select(OutboxEvent)
                .where(OutboxEvent.aggregate_id == str(availability_id))
                .order_by(OutboxEvent.occurred_at.desc())
            )
            .scalars()
            .first()
        )


def trace_id_of(traceparent: str) -> str:
    return traceparent.split("-")[1]


def test_outside_a_trace_a_row_has_no_traceparent(departure):
    assert newest_event(departure(total_slots=3)).traceparent is None


def test_a_row_keeps_the_trace_of_the_transaction_that_wrote_it(departure):
    with tracer.start_as_current_span("booking") as span:
        availability_id = departure(total_slots=3)
        expected = format(span.get_span_context().trace_id, "032x")
    assert trace_id_of(newest_event(availability_id).traceparent) == expected


def test_the_relay_hands_the_traceparent_to_the_publisher(departure):
    class Recording:
        messages = []

        def publish(self, messages):
            self.messages.extend(messages)

    with tracer.start_as_current_span("booking"):
        availability_id = departure(total_slots=3)
        written = current_traceparent()
    event = newest_event(availability_id)
    publisher = Recording()
    relay_batch(SessionLocal, publisher, 100)

    relayed = next(m for m in publisher.messages if m.event_id == event.id)
    assert trace_id_of(relayed.traceparent) == trace_id_of(written)


def test_continuing_restores_the_context_afterwards():
    with tracer.start_as_current_span("booking"):
        stored = current_traceparent()
    assert current_traceparent() is None
    with continuing(stored):
        assert trace_id_of(current_traceparent()) == trace_id_of(stored)
    assert current_traceparent() is None


def test_continuing_without_a_traceparent_changes_nothing():
    with tracer.start_as_current_span("relay"):
        before = current_traceparent()
        with continuing(None):
            assert current_traceparent() == before


def test_log_lines_carry_the_trace_service_and_version():
    record = logging.LogRecord("app", logging.INFO, __file__, 1, "hello %s", ("there",), None)
    with tracer.start_as_current_span("booking") as span:
        _TraceContext().filter(record)
        expected = format(span.get_span_context().trace_id, "032x")

    entry = json.loads(JsonFormatter().format(record))
    assert entry["message"] == "hello there"
    assert entry["trace_id"] == expected
    assert len(entry["span_id"]) == 16
    assert {"service", "version", "ts", "level", "logger"} <= entry.keys()


def test_a_log_line_outside_a_trace_has_no_trace_fields():
    record = logging.LogRecord("app", logging.INFO, __file__, 1, "hello", (), None)
    _TraceContext().filter(record)
    entry = json.loads(JsonFormatter().format(record))
    assert "trace_id" not in entry


@pytest.mark.skipif(not RABBITMQ_URL, reason="TEST_RABBITMQ_URL not set; needs a real RabbitMQ")
def test_the_trace_id_survives_the_hop_through_a_real_broker(departure):
    """HTTP transaction -> outbox row -> relay -> RabbitMQ: the consumer sees the trace the transaction started."""
    import pika

    from app.messaging import RabbitPublisher

    exchange = f"test.events.{uuid.uuid4().hex[:8]}"
    connection = pika.BlockingConnection(pika.URLParameters(RABBITMQ_URL))
    channel = connection.channel()
    channel.exchange_declare(exchange, exchange_type="topic", durable=True)
    queue = channel.queue_declare("", exclusive=True).method.queue
    channel.queue_bind(queue, exchange, routing_key="availability.*")

    with tracer.start_as_current_span("POST /webhooks/stripe") as span:
        event_id = newest_event(departure(total_slots=3)).id
        expected = format(span.get_span_context().trace_id, "032x")

    publisher = RabbitPublisher(RABBITMQ_URL, exchange)
    try:
        relay_batch(SessionLocal, publisher, 100)
    finally:
        publisher.close()

    method, properties, _ = channel.basic_get(queue, auto_ack=True)
    channel.exchange_delete(exchange)
    connection.close()
    assert method is not None, "nothing was routed to the bound queue"
    assert properties.message_id == str(event_id)
    assert trace_id_of(properties.headers["traceparent"]) == expected
