from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from ..config import Settings, get_settings
from ..database import get_db
from ..schemas import CheckInRequest, CheckInResponse
from ..security import CurrentUser, require_roles
from ..services import tickets

router = APIRouter(prefix="/api/check-ins", tags=["check-in"])


@router.post(
    "",
    response_model=CheckInResponse,
    responses={
        404: {"description": "No booking matches the ticket"},
        409: {"description": "Not confirmed, another day, or already checked in (ticket-wrong-day, "
                           "ticket-already-used, booking-not-confirmed)"},
        410: {"description": "The ticket has expired (ticket-expired)"},
        422: {"description": "Tampered, foreign or malformed ticket (ticket-invalid)"},
    },
)  # fmt: skip
def check_in(
    body: CheckInRequest,
    db: Annotated[Session, Depends(get_db)],
    settings: Annotated[Settings, Depends(get_settings)],
    operator: Annotated[CurrentUser, Depends(require_roles("OPERATOR", "ADMIN"))],
) -> CheckInResponse:
    """Scan a ticket's QR code: accepted once, on the day of departure in the tour's time zone."""
    result = tickets.check_in(db, body.token, operator.id, settings)
    booking = result.booking
    return CheckInResponse(
        booking_id=booking.id,
        reference=booking.reference,
        tour_name=booking.availability.tour_name,
        starts_at=booking.availability.starts_at,
        time_zone=booking.availability.time_zone,
        slots=booking.slots,
        checked_in_at=result.checked_in_at,
    )
