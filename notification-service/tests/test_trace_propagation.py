"""The trace crosses the bridge into Celery and on through the render-then-email chain (ADR 0017)."""

import pika
import pytest
from celery.signals import before_task_publish
from opentelemetry import trace
from opentelemetry.instrumentation.celery import CeleryInstrumentor
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from app.config import get_settings
from app.database import SessionLocal
from app.models import OutboxEvent
from app.outbox import record, relay_batch
from app.tasks import render_ticket, send_email
from app.telemetry import current_traceparent

provider = TracerProvider()
spans = InMemorySpanExporter()
provider.add_span_processor(SimpleSpanProcessor(spans))
trace.set_tracer_provider(provider)
tracer = provider.get_tracer("test")


def trace_id_of(traceparent: str) -> str:
    return traceparent.split("-")[1]


@pytest.fixture
def published_headers():
    """The headers of each task as it is published. Not read from the queue: the suite's real worker consumes it."""
    seen: list[dict] = []

    def capture(headers, **_):
        seen.append(dict(headers))

    before_task_publish.connect(capture, weak=False)
    yield seen
    before_task_publish.disconnect(capture)


@pytest.fixture
def instrumented_celery():
    instrumentor = CeleryInstrumentor()
    instrumentor.instrument(tracer_provider=provider)
    yield
    instrumentor.uninstrument()


def test_a_row_keeps_the_trace_and_the_relay_publishes_inside_it():
    settings = get_settings()
    exchange = settings.events_exchange
    connection = pika.BlockingConnection(pika.URLParameters(settings.rabbitmq_url))
    channel = connection.channel()
    channel.exchange_declare(exchange, exchange_type="topic", durable=True)
    queue = channel.queue_declare("", exclusive=True).method.queue
    channel.queue_bind(queue, exchange, routing_key="ticket.trace-test")

    with tracer.start_as_current_span("render") as span:
        with SessionLocal() as db:
            event_id = record(db, aggregate_type="ticket", aggregate_id=1, event_type="ticket.trace-test", data={})
            db.commit()
        expected = format(span.get_span_context().trace_id, "032x")

    with SessionLocal() as db:
        assert trace_id_of(db.get(OutboxEvent, event_id).traceparent) == expected
    with SessionLocal() as db:
        assert relay_batch(db, settings) >= 1

    method, properties, _ = channel.basic_get(queue, auto_ack=True)
    connection.close()
    assert method is not None
    assert trace_id_of(properties.headers["traceparent"]) == expected


def test_a_task_enqueued_inside_a_trace_carries_it(instrumented_celery, published_headers):
    with tracer.start_as_current_span("notification-bridge consume") as span:
        render_ticket.si(1).apply_async()
        expected = format(span.get_span_context().trace_id, "032x")

    assert trace_id_of(published_headers[-1]["traceparent"]) == expected


def test_a_consumer_continues_the_trace_from_the_message_header():
    """What the bridge does with a booking event: PikaInstrumentor wraps its consumer callback."""
    from opentelemetry.instrumentation.pika.utils import _decorate_callback

    seen = {}

    def callback(channel, method, properties, body):
        seen["traceparent"] = current_traceparent()

    with tracer.start_as_current_span("relay publish") as span:
        published = current_traceparent()
        expected = format(span.get_span_context().trace_id, "032x")

    decorated = _decorate_callback(callback, tracer, "notifications.booking-events")
    properties = pika.BasicProperties(headers={"traceparent": published})
    method = pika.spec.Basic.Deliver(exchange="smarttours.events", routing_key="booking.confirmed")
    decorated(_FakeChannel(), method, properties, b"{}")

    assert trace_id_of(seen["traceparent"]) == expected


def test_the_email_step_of_a_chain_stays_in_the_trace(instrumented_celery, published_headers):
    """The chain enqueues send_email from inside render_ticket: same trace id, so render -> email is one trace."""
    with tracer.start_as_current_span("render_ticket") as span:
        send_email.si(1).apply_async()
        expected = format(span.get_span_context().trace_id, "032x")

    assert trace_id_of(published_headers[-1]["traceparent"]) == expected


class _FakeChannel:
    class connection:  # noqa: N801
        class _impl:  # noqa: N801
            params = pika.ConnectionParameters(host="broker")
