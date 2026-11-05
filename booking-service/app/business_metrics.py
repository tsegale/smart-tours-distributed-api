"""What the booking funnel dashboard reads (ADR 0017). Pushed over OTLP; without an exporter these do nothing."""

from opentelemetry import metrics

_meter = metrics.get_meter("smarttours.booking")

holds_created = _meter.create_counter("booking.holds", description="Bookings started: seats held, awaiting payment")
status_changes = _meter.create_counter(
    "booking.status.changes", description="Bookings moving to a status: the holds' outcomes"
)
seat_conflicts = _meter.create_counter(
    "booking.seat.conflicts", description="Seat reservations refused: busy (retryable) or not_enough_seats"
)
