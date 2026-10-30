"""Applies verified payment-provider events to bookings, exactly once per event id.

The event id is inserted into processed_webhook_events in the same transaction as the state
change. A redelivery either sees the committed row and does nothing, or, if it arrives while
the first delivery is still running, waits on the unique index and then does nothing. If the
state change fails, the whole transaction rolls back, the endpoint answers 5xx, and the
provider redelivers later.

Refunds the events make necessary are committed as REFUND_PENDING here and executed after
commit by the caller (never with a lock held). The sweeper retries any that fail.
"""

import logging
from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from ..domain.booking_status import transition
from ..domain.money import to_minor_units
from ..models import Booking, BookingStatus, Payment, PaymentStatus, ProcessedWebhookEvent, TourAvailability
from ..payments.base import PaymentProvider, ProviderEvent, ProviderEventType
from . import outbox
from .booking_service import lock_booking
from .reservation import lock_availability

log = logging.getLogger(__name__)

E = ProviderEventType


@dataclass
class WebhookResult:
    duplicate: bool = False
    booking_id: int | None = None
    refunds_due: list[int] = field(default_factory=list)


def handle_event(db: Session, event: ProviderEvent, provider: PaymentProvider) -> WebhookResult:
    try:
        result = _apply(db, event, provider)
        if result.duplicate:
            db.rollback()
        else:
            db.commit()
        return result
    except Exception:
        db.rollback()
        raise


def _apply(db: Session, event: ProviderEvent, provider: PaymentProvider) -> WebhookResult:
    recorded = db.execute(
        insert(ProcessedWebhookEvent)
        .values(event_id=event.id, provider=provider.name, event_type=event.type)
        .on_conflict_do_nothing(index_elements=[ProcessedWebhookEvent.event_id])
        .returning(ProcessedWebhookEvent.event_id)
    ).scalar_one_or_none()
    if recorded is None:
        log.info("webhook.duplicate event_id=%s type=%s", event.id, event.type)
        return WebhookResult(duplicate=True)

    if event.type not in {e.value for e in E}:
        log.debug("webhook.ignored event_id=%s type=%s", event.id, event.type)
        return WebhookResult()

    booking_id = _resolve_booking_id(db, event)
    if booking_id is None:
        log.warning("webhook.unmatched event_id=%s type=%s checkout=%s", event.id, event.type, event.checkout_id)
        return WebhookResult()

    availability_id = db.execute(select(Booking.availability_id).where(Booking.id == booking_id)).scalar_one()
    # Lock order: availability -> booking, like every other path that touches seats.
    availability = lock_availability(db, availability_id)
    booking = lock_booking(db, booking_id)
    payment = _ensure_payment(db, booking, event, provider)
    result = WebhookResult(booking_id=booking_id)
    seats_before = availability.booked_slots

    match event.type:
        case E.CHECKOUT_COMPLETED if event.payment_status == "paid":
            _payment_succeeded(availability, booking, payment, event, result)
        case E.CHECKOUT_COMPLETED:
            # Delayed payment methods complete the session unpaid; async_payment_* follows.
            log.info("webhook.awaiting_async_payment booking_id=%s", booking.id)
        case E.ASYNC_PAYMENT_SUCCEEDED:
            _payment_succeeded(availability, booking, payment, event, result)
        case E.ASYNC_PAYMENT_FAILED:
            payment.status = PaymentStatus.FAILED
            if booking.status == BookingStatus.PENDING_PAYMENT:
                transition(booking, BookingStatus.CANCELLED, reason="async_payment_failed")
                availability.booked_slots -= booking.slots
        case E.CHECKOUT_EXPIRED:
            if payment.status == PaymentStatus.PENDING:
                payment.status = PaymentStatus.EXPIRED
            if booking.status == BookingStatus.PENDING_PAYMENT:
                transition(booking, BookingStatus.EXPIRED, reason="checkout_expired")
                availability.booked_slots -= booking.slots
    if availability.booked_slots != seats_before:
        outbox.availability_changed(db, availability)
    return result


def _payment_succeeded(
    availability: TourAvailability,
    booking: Booking,
    payment: Payment,
    event: ProviderEvent,
    result: WebhookResult,
) -> None:
    if event.payment_id:
        payment.provider_payment_id = event.payment_id
    if event.amount_minor is not None and event.amount_minor != payment.amount_minor:
        log.error(
            "webhook.amount_mismatch booking_id=%s expected=%s got=%s",
            booking.id,
            payment.amount_minor,
            event.amount_minor,
        )

    match BookingStatus(booking.status):
        case BookingStatus.PENDING_PAYMENT:
            payment.status = PaymentStatus.PAID
            transition(booking, BookingStatus.CONFIRMED, reason="payment_succeeded")
        case BookingStatus.EXPIRED if availability.slots_remaining >= booking.slots:
            # Paid after the hold lapsed, and the seats are still free: take them again.
            availability.booked_slots += booking.slots
            payment.status = PaymentStatus.PAID
            transition(booking, BookingStatus.CONFIRMED, reason="late_payment_seats_rereserved")
        case BookingStatus.EXPIRED:
            # Paid after the hold lapsed, and someone else has the seats: give the money back.
            payment.status = PaymentStatus.REFUND_PENDING
            transition(booking, BookingStatus.REFUNDED, reason="late_payment_sold_out")
            result.refunds_due.append(booking.id)
        case BookingStatus.CANCELLED:
            payment.status = PaymentStatus.REFUND_PENDING
            transition(booking, BookingStatus.REFUNDED, reason="paid_after_cancellation")
            result.refunds_due.append(booking.id)
        case BookingStatus.CONFIRMED | BookingStatus.REFUNDED:
            pass  # already settled; a second success event for the same session


def _resolve_booking_id(db: Session, event: ProviderEvent) -> int | None:
    if event.checkout_id:
        found = db.execute(
            select(Payment.booking_id).where(Payment.provider_checkout_id == event.checkout_id)
        ).scalar_one_or_none()
        if found is not None:
            return found
    if event.booking_id is not None and db.get(Booking, event.booking_id) is not None:
        return event.booking_id
    return None


def _ensure_payment(db: Session, booking: Booking, event: ProviderEvent, provider: PaymentProvider) -> Payment:
    """The payment row, or one rebuilt from the event if we crashed between checkout and storing it."""
    payment = db.execute(
        select(Payment).where(Payment.booking_id == booking.id).execution_options(populate_existing=True)
    ).scalar_one_or_none()
    if payment is not None:
        return payment
    log.warning("webhook.payment_recovered booking_id=%s checkout=%s", booking.id, event.checkout_id)
    payment = Payment(
        booking_id=booking.id,
        provider=provider.name,
        provider_checkout_id=event.checkout_id or f"unknown-{event.id}",
        status=PaymentStatus.PENDING,
        amount_minor=event.amount_minor or to_minor_units(booking.total_amount, booking.currency),
        currency=event.currency or booking.currency,
    )
    db.add(payment)
    db.flush()
    return payment
