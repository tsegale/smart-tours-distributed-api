from typing import Annotated

from fastapi import APIRouter, Depends, Header, Response, status
from sqlalchemy.orm import Session

from ..database import get_db
from ..models import Booking
from ..schemas import BookingCreate, BookingResponse
from ..security import CurrentUser, get_current_user
from ..services import booking_service

router = APIRouter(prefix="/api/bookings", tags=["bookings"])

DbSession = Annotated[Session, Depends(get_db)]
User = Annotated[CurrentUser, Depends(get_current_user)]


def _to_response(booking: Booking) -> BookingResponse:
    return BookingResponse(
        id=booking.id,
        availability_id=booking.availability_id,
        tour_id=booking.availability.tour_id,
        starts_at=booking.availability.starts_at,
        slots=booking.slots,
        status=booking.status,
        created_at=booking.created_at,
        cancelled_at=booking.cancelled_at,
    )


@router.post(
    "",
    status_code=status.HTTP_201_CREATED,
    response_model=BookingResponse,
    responses={
        200: {"description": "Idempotent replay: the booking created by an earlier identical request"},
        409: {"description": "Not enough slots, or the departure is busy"},
    },
)
def create_booking(
    body: BookingCreate,
    user: User,
    db: DbSession,
    response: Response,
    idempotency_key: Annotated[str | None, Header(max_length=255)] = None,
) -> BookingResponse:
    booking, created = booking_service.create_booking(db, user.id, body, idempotency_key)
    if not created:
        response.status_code = status.HTTP_200_OK
    response.headers["Location"] = f"/api/bookings/{booking.id}"
    return _to_response(booking)


@router.get("/me", response_model=list[BookingResponse])
def my_bookings(user: User, db: DbSession) -> list[BookingResponse]:
    return [_to_response(b) for b in booking_service.list_bookings_for_user(db, user.id)]


@router.get("/{booking_id}", response_model=BookingResponse)
def get_booking(booking_id: int, user: User, db: DbSession) -> BookingResponse:
    return _to_response(booking_service.get_booking_for_user(db, user.id, booking_id))


@router.post("/{booking_id}/cancel", response_model=BookingResponse)
def cancel_booking(booking_id: int, user: User, db: DbSession) -> BookingResponse:
    return _to_response(booking_service.cancel_booking(db, user.id, booking_id))
