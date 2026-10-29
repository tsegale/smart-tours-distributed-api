"""Hold-then-pay booking saga (ADR 0006).

    1. hold_seats():      take seats and create a PENDING_PAYMENT booking, committed.
    2. start_checkout():  open the provider checkout, outside any lock. On failure, compensate:
                          release the hold and answer 502.
    3. webhooks (app.services.webhook_service) confirm, expire or refund.
    4. the sweeper (app.services.sweeper) expires holds nobody paid for.

Lock order is always availability -> booking on every path that touches seats, so no two
paths can deadlock. Payment rows are only changed while their booking is locked, except the
REFUND_PENDING -> REFUNDED step, which nothing else writes.
"""

import logging
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..clients.tour_client import TourClient
from ..config import Settings
from ..domain.booking_status import ensure_can_transition, transition
from ..errors import (
    ConflictError,
    IdempotencyKeyReusedError,
    NotEnoughSlotsError,
    NotFoundError,
    PaymentPendingError,
    PaymentProviderError,
)
from ..models import Booking, BookingStatus, Payment, PaymentStatus, TourAvailability
from ..payments.base import CheckoutAlreadyCompletedError, CheckoutRequest, PaymentProvider, PaymentRef
from ..schemas import AvailabilityCreate, BookingCreate
from .reservation import lock_availability

log = logging.getLogger(__name__)

# Slack between our clock and the provider's when sizing a hold to the provider's minimum.
CHECKOUT_TTL_MARGIN = timedelta(minutes=2)


# ---------------------------------------------------------------- departures


def create_availability(db: Session, data: AvailabilityCreate, tours: TourClient) -> TourAvailability:
    # Validate the tour and snapshot its price before opening a transaction: no lock or
    # connection is held while waiting on another service.
    tour = tours.get_tour(data.tour_id)
    availability = TourAvailability(
        tour_id=tour.id,
        tour_name=tour.name,
        unit_price=tour.price,
        currency=tour.currency,
        starts_at=data.starts_at,
        total_slots=data.total_slots,
    )
    db.add(availability)
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise ConflictError("This tour already has a departure at that time") from exc
    return availability


def list_availability(db: Session, tour_id: int) -> list[TourAvailability]:
    stmt = select(TourAvailability).where(TourAvailability.tour_id == tour_id).order_by(TourAvailability.starts_at)
    return list(db.execute(stmt).scalars())


# ---------------------------------------------------------------- holds and checkout


def hold_ttl(settings: Settings, provider: PaymentProvider) -> timedelta:
    """HOLD_MINUTES, stretched or capped to what the provider allows for a checkout's lifetime.

    The checkout expires exactly when the hold does, so the hold is never shorter than the session.
    """
    ttl = timedelta(minutes=settings.hold_minutes)
    if provider.min_checkout_ttl > timedelta(0):
        ttl = max(ttl, provider.min_checkout_ttl + CHECKOUT_TTL_MARGIN)
    return min(ttl, provider.max_checkout_ttl - CHECKOUT_TTL_MARGIN)


def _find_by_idempotency_key(db: Session, user_id: str, key: str) -> Booking | None:
    return db.execute(
        select(Booking).where(Booking.user_id == user_id, Booking.idempotency_key == key)
    ).scalar_one_or_none()


def _replay(existing: Booking, data: BookingCreate) -> Booking:
    if existing.availability_id != data.availability_id or existing.slots != data.slots:
        raise IdempotencyKeyReusedError("This Idempotency-Key was already used for a different booking request")
    return existing


def hold_seats(
    db: Session,
    user_id: str,
    data: BookingCreate,
    idempotency_key: str | None,
    ttl: timedelta,
) -> tuple[Booking, bool]:
    """Reserve seats as a PENDING_PAYMENT hold. Returns (booking, created); False on a replay."""
    try:
        if idempotency_key:
            existing = _find_by_idempotency_key(db, user_id, idempotency_key)
            if existing is not None:
                db.rollback()
                return _replay(existing, data), False

        availability = lock_availability(db, data.availability_id)
        if data.slots > availability.slots_remaining:
            raise NotEnoughSlotsError(
                f"Requested {data.slots} slots but only {availability.slots_remaining} remain",
                slots_remaining=availability.slots_remaining,
            )
        availability.booked_slots += data.slots
        booking = Booking(
            user_id=user_id,
            availability_id=availability.id,
            slots=data.slots,
            status=BookingStatus.PENDING_PAYMENT,
            unit_price=availability.unit_price,
            total_amount=availability.unit_price * data.slots,
            currency=availability.currency,
            idempotency_key=idempotency_key,
            hold_expires_at=datetime.now(UTC) + ttl,
        )
        db.add(booking)
        db.commit()
        db.refresh(booking)
        log.info(
            "booking.transition booking_id=%s from=none to=%s reason=seats_held",
            booking.id,
            BookingStatus.PENDING_PAYMENT,
        )
        return booking, True
    except IntegrityError as exc:
        db.rollback()  # also gives the seats back
        if idempotency_key:
            # The same key raced itself; the unique constraint let exactly one attempt through.
            existing = _find_by_idempotency_key(db, user_id, idempotency_key)
            if existing is not None:
                return _replay(existing, data), False
        raise ConflictError("The booking conflicts with existing data") from exc
    except Exception:
        db.rollback()
        raise


def lock_booking(db: Session, booking_id: int) -> Booking:
    # of=Booking: Postgres can't lock the nullable side of the outer join that eager-loads the availability.
    return db.execute(
        select(Booking)
        .where(Booking.id == booking_id)
        .with_for_update(of=Booking)
        .execution_options(populate_existing=True)
    ).scalar_one()


def _payment_for(db: Session, booking_id: int) -> Payment | None:
    return db.execute(
        select(Payment).where(Payment.booking_id == booking_id).execution_options(populate_existing=True)
    ).scalar_one_or_none()


def _release_seats(availability: TourAvailability, booking: Booking) -> None:
    availability.booked_slots -= booking.slots  # the CHECK constraint stops this going negative


def start_checkout(db: Session, booking: Booking, provider: PaymentProvider, settings: Settings) -> Booking:
    """Open the provider checkout for a committed hold. Never called with a lock held."""
    booking_id = booking.id
    availability = booking.availability
    request = CheckoutRequest(
        booking_id=booking_id,
        description=f"{availability.tour_name}, departing {availability.starts_at.astimezone(UTC):%d %b %Y %H:%M} UTC",
        quantity=booking.slots,
        unit_amount=booking.unit_price,
        currency=booking.currency,
        # From stored state only, so a retried call sends identical parameters under the same
        # idempotency key and the provider returns the original session.
        expires_at=booking.hold_expires_at or datetime.now(UTC),
        success_url=settings.checkout_success_url.format(booking_id=booking_id),
        cancel_url=settings.checkout_cancel_url.format(booking_id=booking_id),
    )
    # End the read transaction: no pooled connection sits idle in a transaction while the
    # provider takes its time (up to the SDK timeout, times its retries).
    db.rollback()
    try:
        session = provider.create_checkout(request)
    except PaymentProviderError:
        _compensate_failed_checkout(db, booking_id)
        raise

    try:
        lock_booking(db, booking_id)
        db.execute(
            insert(Payment)
            .values(
                booking_id=booking_id,
                provider=provider.name,
                provider_checkout_id=session.id,
                checkout_url=session.url,
                status=PaymentStatus.PENDING,
                amount_minor=session.amount_minor,
                currency=session.currency,
            )
            # A concurrent idempotent replay may have stored the same session first.
            .on_conflict_do_nothing(index_elements=[Payment.booking_id])
        )
        db.commit()
    except Exception:
        db.rollback()
        raise
    db.refresh(booking)
    return booking


def _compensate_failed_checkout(db: Session, booking_id: int) -> None:
    """Saga compensation: the hold can't be paid for, so give the seats back straight away."""
    try:
        booking = db.get(Booking, booking_id)
        if booking is None:
            return
        availability = lock_availability(db, booking.availability_id)
        booking = lock_booking(db, booking_id)
        if booking.status == BookingStatus.PENDING_PAYMENT:
            transition(booking, BookingStatus.CANCELLED, reason="checkout_failed")
            _release_seats(availability, booking)
            # The attempt left nothing behind, so the client may retry with the same key.
            booking.idempotency_key = None
        db.commit()
    except Exception:
        db.rollback()
        log.exception("Compensation failed for booking %s; the sweeper will release it at hold expiry", booking_id)


def ensure_checkout(db: Session, booking: Booking, provider: PaymentProvider, settings: Settings) -> Booking:
    """Idempotent replay of a hold whose checkout was never opened (e.g. a crash in between)."""
    if booking.status == BookingStatus.PENDING_PAYMENT and booking.payment is None:
        return start_checkout(db, booking, provider, settings)
    return booking


# ---------------------------------------------------------------- reads


def get_booking_for_user(db: Session, user_id: str, booking_id: int) -> Booking:
    booking = db.get(Booking, booking_id, populate_existing=True)
    # Another user's booking gets the same 404 as a missing one, so ids can't be probed.
    if booking is None or booking.user_id != user_id:
        raise NotFoundError(f"Booking {booking_id} does not exist", booking_id=booking_id)
    return booking


def list_bookings_for_user(db: Session, user_id: str) -> list[Booking]:
    stmt = select(Booking).where(Booking.user_id == user_id).order_by(Booking.created_at.desc())
    return list(db.execute(stmt).scalars())


# ---------------------------------------------------------------- cancellation and refunds


@dataclass(frozen=True)
class CancellationOutcome:
    policy: str  # "hold_released", "full_refund" or "no_refund"
    refund_amount: Decimal | None
    refund_status: str | None  # payment status after the attempt: REFUNDED or REFUND_PENDING
    explanation: str


def cancel_booking(
    db: Session, user_id: str, booking_id: int, provider: PaymentProvider, settings: Settings
) -> tuple[Booking, CancellationOutcome | None]:
    """Cancel a hold or a confirmed booking. Cancelling twice is a no-op, not an error."""
    booking = get_booking_for_user(db, user_id, booking_id)
    status = BookingStatus(booking.status)
    payment = booking.payment
    open_checkout = (
        payment.provider_checkout_id if payment is not None and payment.status == PaymentStatus.PENDING else None
    )
    availability_id = booking.availability_id
    db.rollback()  # no transaction stays open while the provider is called

    if status in (BookingStatus.CANCELLED, BookingStatus.REFUNDED):
        return booking, None
    if status == BookingStatus.PENDING_PAYMENT:
        return _cancel_hold(db, booking_id, availability_id, open_checkout, provider)
    if status == BookingStatus.CONFIRMED:
        return _cancel_confirmed(db, booking_id, availability_id, provider, settings)
    ensure_can_transition(status, BookingStatus.CANCELLED, booking_id)  # EXPIRED: 409 Problem
    return booking, None


def _cancel_hold(
    db: Session, booking_id: int, availability_id: int, open_checkout: str | None, provider: PaymentProvider
) -> tuple[Booking, CancellationOutcome]:
    # Close the checkout first, outside any lock. If the customer already paid, the booking is
    # about to be confirmed by the webhook, so refuse rather than release seats that are sold.
    if open_checkout is not None:
        try:
            provider.expire_checkout(open_checkout)
        except CheckoutAlreadyCompletedError as exc:
            raise PaymentPendingError(
                "Payment for this booking has just completed; cancel again once it is confirmed",
                booking_id=booking_id,
                retryable=True,
            ) from exc
    try:
        availability = lock_availability(db, availability_id)
        booking = lock_booking(db, booking_id)
        if booking.status != BookingStatus.PENDING_PAYMENT:
            raise ConflictError(f"Booking is now {booking.status}; please retry", current_status=booking.status)
        transition(booking, BookingStatus.CANCELLED, reason="customer_cancelled_hold")
        _release_seats(availability, booking)
        payment = _payment_for(db, booking_id)
        if payment is not None and payment.status == PaymentStatus.PENDING:
            payment.status = PaymentStatus.EXPIRED
        db.commit()
    except Exception:
        db.rollback()
        raise
    db.refresh(booking)
    return booking, CancellationOutcome(
        "hold_released", None, None, "The unpaid hold was released; nothing was charged."
    )


def refund_eligible(starts_at: datetime, settings: Settings, now: datetime | None = None) -> bool:
    now = now or datetime.now(UTC)
    return starts_at - now > timedelta(hours=settings.cancellation_full_refund_hours)


def _cancel_confirmed(
    db: Session, booking_id: int, availability_id: int, provider: PaymentProvider, settings: Settings
) -> tuple[Booking, CancellationOutcome]:
    hours = settings.cancellation_full_refund_hours
    try:
        availability = lock_availability(db, availability_id)
        booking = lock_booking(db, booking_id)
        if booking.status != BookingStatus.CONFIRMED:
            raise ConflictError(f"Booking is now {booking.status}; please retry", current_status=booking.status)
        payment = _payment_for(db, booking_id)
        paid = payment is not None and payment.status == PaymentStatus.PAID
        refund_due = paid and refund_eligible(availability.starts_at, settings)
        if refund_due:
            transition(booking, BookingStatus.REFUNDED, reason="customer_cancelled_full_refund")
            payment.status = PaymentStatus.REFUND_PENDING
        else:
            transition(booking, BookingStatus.CANCELLED, reason="customer_cancelled_no_refund")
        _release_seats(availability, booking)
        total_amount = booking.total_amount
        db.commit()
    except Exception:
        db.rollback()
        raise

    if refund_due:
        status = issue_refund(db, booking_id, provider)
        outcome = CancellationOutcome(
            "full_refund",
            total_amount,
            status,
            f"Cancelled more than {hours} hours before departure: the full amount is refunded.",
        )
    elif not paid:
        outcome = CancellationOutcome("no_refund", None, None, "Cancelled. No payment is on record for this booking.")
    else:
        outcome = CancellationOutcome(
            "no_refund",
            None,
            None,
            f"Cancelled {hours} hours or less before departure: no refund under the cancellation policy.",
        )
    db.refresh(booking)
    return booking, outcome


def issue_refund(db: Session, booking_id: int, provider: PaymentProvider) -> str:
    """Call the provider for a REFUND_PENDING payment. Safe to repeat (idempotency key per booking).

    On failure the payment stays REFUND_PENDING and the sweeper retries it.
    """
    payment = _payment_for(db, booking_id)
    if payment is None or payment.status != PaymentStatus.REFUND_PENDING:
        status = payment.status if payment is not None else "NONE"
        db.rollback()
        return status
    payment_id = payment.id
    ref = PaymentRef(
        booking_id=booking_id,
        checkout_id=payment.provider_checkout_id,
        payment_id=payment.provider_payment_id,
        amount_minor=payment.amount_minor,
        currency=payment.currency,
    )
    db.rollback()  # no transaction stays open while the provider is called
    try:
        refund = provider.refund(ref)
    except PaymentProviderError:
        log.exception("Refund for booking %s failed; left REFUND_PENDING for retry", booking_id)
        return PaymentStatus.REFUND_PENDING
    try:
        db.execute(
            update(Payment)
            .where(Payment.id == payment_id, Payment.status == PaymentStatus.REFUND_PENDING)
            .values(status=PaymentStatus.REFUNDED, provider_refund_id=refund.id)
        )
        db.commit()
    except Exception:
        db.rollback()
        raise
    log.info("payment.refunded booking_id=%s refund_id=%s provider_status=%s", booking_id, refund.id, refund.status)
    return PaymentStatus.REFUNDED
