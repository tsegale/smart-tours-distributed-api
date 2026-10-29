from functools import lru_cache
from typing import Annotated

from fastapi import APIRouter, Depends, Header, Response, status
from sqlalchemy.orm import Session

from ..config import Settings, get_settings
from ..database import get_db
from ..models import Booking, BookingStatus
from ..payments import get_payment_provider
from ..payments.base import PaymentProvider
from ..schemas import BookingCreate, BookingResponse, CancellationSummary, PaymentSummary
from ..security import CurrentUser, get_current_user
from ..services import booking_service
from ..services.booking_service import CancellationOutcome
from ..services.reservation import SeatReservationStrategy, strategy_from_settings

router = APIRouter(prefix="/api/bookings", tags=["bookings"])

DbSession = Annotated[Session, Depends(get_db)]
User = Annotated[CurrentUser, Depends(get_current_user)]
Provider = Annotated[PaymentProvider, Depends(get_payment_provider)]
AppSettings = Annotated[Settings, Depends(get_settings)]


@lru_cache
def get_reservation_strategy() -> SeatReservationStrategy:
    return strategy_from_settings(get_settings())


Strategy = Annotated[SeatReservationStrategy, Depends(get_reservation_strategy)]


def respond(db: Session, booking: Booking, cancellation: CancellationOutcome | None = None) -> BookingResponse:
    """Build the response, then end the read transaction so no connection outlives the endpoint."""
    try:
        return to_response(booking, cancellation)
    finally:
        db.rollback()


def to_response(booking: Booking, cancellation: CancellationOutcome | None = None) -> BookingResponse:
    payment = booking.payment
    return BookingResponse(
        id=booking.id,
        availability_id=booking.availability_id,
        tour_id=booking.availability.tour_id,
        starts_at=booking.availability.starts_at,
        slots=booking.slots,
        status=booking.status,
        unit_price=booking.unit_price,
        total_amount=booking.total_amount,
        currency=booking.currency,
        hold_expires_at=booking.hold_expires_at if booking.status == BookingStatus.PENDING_PAYMENT else None,
        checkout_url=payment.checkout_url
        if payment is not None and booking.status == BookingStatus.PENDING_PAYMENT
        else None,
        payment=PaymentSummary(
            provider=payment.provider,
            status=payment.status,
            amount_minor=payment.amount_minor,
            currency=payment.currency,
        )
        if payment is not None
        else None,
        created_at=booking.created_at,
        confirmed_at=booking.confirmed_at,
        cancelled_at=booking.cancelled_at,
        cancellation=CancellationSummary(**cancellation.__dict__) if cancellation is not None else None,
    )


@router.post(
    "",
    status_code=status.HTTP_201_CREATED,
    response_model=BookingResponse,
    responses={
        200: {"description": "Idempotent replay: the booking created by an earlier identical request"},
        409: {"description": "Not enough slots, or the departure is busy"},
        502: {"description": "The payment provider could not start checkout; the hold was released"},
    },
)
def create_booking(
    body: BookingCreate,
    user: User,
    db: DbSession,
    response: Response,
    strategy: Strategy,
    provider: Provider,
    settings: AppSettings,
    idempotency_key: Annotated[str | None, Header(max_length=255)] = None,
) -> BookingResponse:
    """Hold seats for HOLD_MINUTES and return the checkout URL to pay at."""
    ttl = booking_service.hold_ttl(settings, provider)
    booking, created = booking_service.hold_seats(db, user.id, body, idempotency_key, strategy, ttl)
    # The hold is committed before the provider is called: no lock is held across the network.
    booking = booking_service.ensure_checkout(db, booking, provider, settings)
    if not created:
        response.status_code = status.HTTP_200_OK
    response.headers["Location"] = f"/api/bookings/{booking.id}"
    return respond(db, booking)


@router.get("/me", response_model=list[BookingResponse])
def my_bookings(user: User, db: DbSession) -> list[BookingResponse]:
    try:
        return [to_response(b) for b in booking_service.list_bookings_for_user(db, user.id)]
    finally:
        db.rollback()


@router.get("/{booking_id}", response_model=BookingResponse)
def get_booking(booking_id: int, user: User, db: DbSession) -> BookingResponse:
    return respond(db, booking_service.get_booking_for_user(db, user.id, booking_id))


@router.post(
    "/{booking_id}/cancel",
    response_model=BookingResponse,
    responses={409: {"description": "Expired bookings can't be cancelled, or a payment is completing right now"}},
)
def cancel_booking(
    booking_id: int, user: User, db: DbSession, provider: Provider, settings: AppSettings
) -> BookingResponse:
    """Release a hold, or cancel a confirmed booking under the refund policy (explained in `cancellation`)."""
    booking, outcome = booking_service.cancel_booking(db, user.id, booking_id, provider, settings)
    return respond(db, booking, outcome)
