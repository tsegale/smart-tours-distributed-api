"""Taking a hold and paying for it are two traces, minutes apart. The payment's links to the hold's (ADR 0017)."""

from datetime import timedelta

from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from sqlalchemy import update

from app.database import SessionLocal
from app.models import Booking
from app.payments import get_payment_provider
from app.schemas import BookingCreate
from app.services.booking_service import hold_seats
from app.services.reservation import AtomicReservation
from app.services.webhook_service import handle_event
from app.telemetry import link_current_span_to
from tests.stripe_events import checkout_event, signature

COMPLETED = "checkout.session.completed"

spans = InMemorySpanExporter()
provider = TracerProvider()
provider.add_span_processor(SimpleSpanProcessor(spans))
tracer = provider.get_tracer("test")


def trace_id(span) -> str:
    return format(span.get_span_context().trace_id, "032x")


def test_a_hold_keeps_the_trace_of_the_request_that_took_it(departure):
    availability_id = departure(total_slots=3)
    with tracer.start_as_current_span("POST /api/bookings") as span, SessionLocal() as db:
        booking, _ = hold_seats(
            db, "alice", BookingCreate(availability_id=availability_id, slots=1), None, AtomicReservation(),
            timedelta(minutes=30),
        )  # fmt: skip
        assert trace_id(span) in booking.hold_traceparent


def test_a_hold_taken_outside_a_trace_stores_nothing(departure, book):
    with SessionLocal() as db:
        assert db.get(Booking, book(departure())["id"]).hold_traceparent is None


def test_the_payment_links_to_the_hold(departure, book):
    booking_id = book(departure())["id"]
    with tracer.start_as_current_span("POST /api/bookings") as hold:
        hold_context = hold.get_span_context()
        hold_traceparent = f"00-{format(hold_context.trace_id, '032x')}-{format(hold_context.span_id, '016x')}-01"
    with SessionLocal() as db:
        db.execute(update(Booking).where(Booking.id == booking_id).values(hold_traceparent=hold_traceparent))
        db.commit()

    body = checkout_event(COMPLETED, booking_id)
    payments = get_payment_provider()
    event = payments.parse_webhook(body, signature(body))
    with tracer.start_as_current_span("POST /webhooks/stripe") as webhook, SessionLocal() as db:
        handle_event(db, event, payments)

    assert webhook.get_span_context().trace_id != hold_context.trace_id, "two traces, not one"
    assert [link.context.trace_id for link in webhook.links] == [hold_context.trace_id]
    assert [link.context.span_id for link in webhook.links] == [hold_context.span_id]
    assert webhook.links[0].attributes["link.reason"] == "booking.hold"
    with SessionLocal() as db:
        assert db.get(Booking, booking_id).status == "CONFIRMED"


def test_a_booking_without_a_stored_trace_is_paid_as_usual(departure, book):
    booking_id = book(departure())["id"]
    body = checkout_event(COMPLETED, booking_id)
    payments = get_payment_provider()
    with tracer.start_as_current_span("POST /webhooks/stripe") as webhook, SessionLocal() as db:
        handle_event(db, payments.parse_webhook(body, signature(body)), payments)
    assert not webhook.links
    with SessionLocal() as db:
        assert db.get(Booking, booking_id).status == "CONFIRMED"


def test_a_malformed_traceparent_is_ignored():
    with tracer.start_as_current_span("webhook") as webhook:
        link_current_span_to("not-a-traceparent", "booking.hold")
    assert not webhook.links
